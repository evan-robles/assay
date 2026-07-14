import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import pandas as pd

np.random.seed(42)

data = pd.DataFrame(
   {
        "single-point": np.random.uniform(0.7, 1.0, 10), 
        "name-to-smiles": [0.98, 1, 1, 1, 1, 0.56, 1, 1, 0.98, 1],
        "build-from-smiles": [0.98, 1, 1, 1, 0.98, 0.91, 1, 1, 0.98, 0.98],
        "conformer-search": [0.82, 1, 1, 1, 1, 0.96, 1, 1, 1, 1],
        "conformational-analysis": [1, 1, 0.93, 0.96, 0.91, 0.44,  1, 1, 1, 1],
        "solvation": [1, 1, 1, 1, 1, 0.89, 1, 1, 1, 1],
        "vibrational-analysis": [1, 1, 1, 1, 1, 0.96, 1, 1, 1, 0.93],
        "logp-partition": [1, 1, 1, 1, 1, 0.78, 1, 1, 1, 1],
        "electrostatics": [1, 1, 1, 1, 1, 0.98, 1, 1, 1, 1],
        "redox-potential": np.random.uniform(0.7, 1.0, 10),
        "pka-acidity": np.random.uniform(0.7, 1.0, 10),
        "fukui-analysis": [1, 1, 1, 1, 1, 0.93, 1, 1, 1, 1],
        "frontier-orbitals": [1, 1, 1, 1, 0.98, 1, 1, 0.91, 0.93, 1]
    }, index=[
        "argo:claude-haiku-4.5",
        "argo:claude-opus-4.8",
        "argo:claude-sonnet-4.6",
        "argo:gemini-2.5-flash",
        "argo:gemini-2.5-pro",
        "argo:gpt-4.1-nano",
        "argo:gpt-4o",
        "argo:gpt-5.5",
        "argo:o3",
        "argo:o4-mini",
    ],
)

plt.figure(figsize=(14, 6))

sns.heatmap(
    data,
    annot=True,
    fmt=".2f",
    cmap="YlGnBu",
    linewidths=0.5,
)

plt.xticks(rotation=45, ha="right")
plt.tight_layout()
plt.show()
