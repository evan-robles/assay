#!/usr/bin/env python3
"""Isolate WHERE the o3 Unicode-escape corruption enters the pipeline.

Root question: for the sporadic `\\u000394`-style malformed escapes seen only in
o3 tool-call arguments, is the corruption present in the RAW HTTP bytes returned
by argo-proxy (=> argo-proxy or o3's fault), or does it only appear AFTER the
OpenAI SDK parses the response (=> SDK's fault)?

Method: install an httpx response event-hook that dumps the RAW response body
(pre-SDK-parse) for every call, then make repeated o3 chat-completion requests
that force a tool call whose arguments must echo Unicode-heavy text verbatim.
For each response we compare:
  (A) the raw body bytes  (what argo-proxy actually sent)
  (B) the SDK-parsed tool_call.arguments string
If the malformed escape / control chars are in (A): NOT the SDK — it's
argo-proxy or o3 upstream. If (A) is clean but (B) is corrupt: the SDK.

Usage (needs the argo-proxy reachable, i.e. your VPN/laptop setup):
    # Env: anl_env  (or wherever openai + httpx are installed)
    CHEMKIT_LLM_API_KEY=<argo-username> \
    python benchmarks/probe_o3_encoding.py --model argo:o3 --rounds 40

Reads CHEMKIT_LLM_BASE_URL / CHEMKIT_LLM_API_KEY the same way the driver does.
Writes any corrupted raw body to probe_raw_<n>.json for inspection.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Unicode-heavy text the model is asked to echo verbatim in a tool call — the
# same characters that corrupt in the real warnings (Δ minus ± em-dash arrow).
_ECHO_TEXT = ("logP from semi-empirical ΔG_solv differences is screening-grade; "
              "±1 log unit typical. water−octanol; 1 atm→1 M; ΔG*_solv — note.")

_ECHO_TOOL = {
    "type": "function",
    "function": {
        "name": "echo_back",
        "description": "Echo the provided text back exactly, verbatim.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "the text to echo back, character for character"},
            },
            "required": ["text"],
        },
    },
}

_BASE_URL = os.environ.get("CHEMKIT_LLM_BASE_URL", "http://0.0.0.0:60639/v1")
_API_KEY = os.environ.get("CHEMKIT_LLM_API_KEY", "")


def _has_ctrl(s: str) -> bool:
    return any(ord(c) < 0x20 and c not in "\t\n\r" for c in s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="argo:o3")
    ap.add_argument("--rounds", type=int, default=40,
                    help="how many requests to make (corruption is ~6%%, so make enough)")
    args = ap.parse_args()

    if not _API_KEY:
        print("Set CHEMKIT_LLM_API_KEY=<your-argo-username>")
        return 2

    try:
        import httpx
        from openai import OpenAI
    except ImportError as e:
        print(f"need openai + httpx installed: {e}")
        return 2

    # Capture the RAW response body (pre-parse) via an httpx response hook.
    raw_bodies = {}

    def _log_raw(response: "httpx.Response") -> None:
        try:
            response.read()
            raw_bodies["last"] = response.content.decode("utf-8", "replace")
        except Exception as ex:  # pragma: no cover
            raw_bodies["last"] = f"<could not read: {ex}>"

    http_client = httpx.Client(event_hooks={"response": [_log_raw]})
    client = OpenAI(base_url=_BASE_URL, api_key=_API_KEY, http_client=http_client)

    prompt = ("Call echo_back with `text` set to EXACTLY this string, character "
              "for character, preserving every symbol:\n\n" + _ECHO_TEXT)

    n_corrupt_raw = n_corrupt_parsed = n_ok = n_notool = 0
    for i in range(args.rounds):
        raw_bodies.clear()
        try:
            resp = client.chat.completions.create(
                model=args.model,
                messages=[{"role": "user", "content": prompt}],
                tools=[_ECHO_TOOL], tool_choice="auto",
            )
        except Exception as ex:
            print(f"[{i}] request error: {ex}")
            continue

        raw = raw_bodies.get("last", "")

        calls = (resp.choices[0].message.tool_calls or []) if resp.choices else []
        if not calls:
            n_notool += 1
            continue

        # (B) SDK-parsed arguments -> the echoed text.
        argstr = calls[0].function.arguments or ""
        try:
            parsed_sdk = json.loads(argstr).get("text", "")
        except ValueError:
            parsed_sdk = argstr
        sdk_corrupt = _has_ctrl(parsed_sdk)

        # (A) Independently parse the RAW body with stdlib json and pull the same
        # tool-call arguments. If OUR parse of the raw bytes ALSO yields control
        # chars, the corruption is in the bytes argo-proxy sent (proxy/o3). If our
        # raw parse is clean but the SDK's is dirty, the SDK is the culprit. This
        # avoids the regex ambiguity (a valid \uXXXX followed by a hex digit of
        # real text is indistinguishable from a 6-hex escape by pattern alone).
        raw_corrupt = None  # None = couldn't extract from raw
        try:
            body = json.loads(raw)
            raw_args = (body["choices"][0]["message"]["tool_calls"][0]
                        ["function"]["arguments"])
            raw_text = json.loads(raw_args).get("text", "")
            raw_corrupt = _has_ctrl(raw_text)
        except Exception:
            raw_corrupt = None

        if raw_corrupt is True or (raw_corrupt is None and sdk_corrupt):
            # corruption present in the raw bytes (or we couldn't isolate but the
            # result is dirty and the SDK just faithfully parsed it)
            n_corrupt_raw += 1
            fn = f"probe_raw_{i}.json"
            with open(fn, "w") as fh:
                fh.write(raw)
            print(f"[{i}] RAW CORRUPT (argo-proxy/o3 upstream) -> saved {fn}")
        elif sdk_corrupt and raw_corrupt is False:
            n_corrupt_parsed += 1
            print(f"[{i}] SDK CORRUPT (raw bytes clean, SDK parse dirty)")
        else:
            n_ok += 1

    print("\n===== SUMMARY =====")
    print(f"  rounds:            {args.rounds}")
    print(f"  clean:             {n_ok}")
    print(f"  no tool call:      {n_notool}")
    print(f"  RAW corrupt:       {n_corrupt_raw}   (=> argo-proxy or o3 upstream)")
    print(f"  SDK-only corrupt:  {n_corrupt_parsed} (=> OpenAI SDK parsing)")
    if n_corrupt_raw:
        print("\nVERDICT: corruption is in the RAW argo-proxy bytes — it is NOT the "
              "SDK. It is argo-proxy (llm-rosetta translation) or o3 upstream. "
              "Inspect a saved probe_raw_*.json to see the malformed escape.")
    elif n_corrupt_parsed:
        print("\nVERDICT: raw bytes are clean but the SDK produced corrupt text — "
              "the OpenAI SDK / its json handling is the culprit.")
    else:
        print("\nNo corruption reproduced in this batch (it is sporadic ~6%). "
              "Re-run with more --rounds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
