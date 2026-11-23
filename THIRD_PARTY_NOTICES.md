# Third-party notices

MaaTactics source is licensed under AGPL-3.0-only. See LICENSE.

The project integrates an external, separately installed MaaCore runtime from
[MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights)
(AGPL-3.0-only). ABI names and SingleStep JSON behavior are based on the source
commit recorded in upstream.lock.json. No upstream GUI, binary, model weight,
or game asset is included in this repository.

`src/maatactics/copilot-action-aliases.json` contains protocol alias data extracted
from MaaAssistantArknights contributors' `CopilotConfig.cpp` at the commit recorded
inside that file. It remains covered by AGPL-3.0-only; it is used to reject actions
that the upstream parser would otherwise silently skip. The mapping is packaged
with the source attribution and this notice.

MCP Python SDK (MIT), jsonschema (MIT), Pillow 12.3 (MIT-CMU), and their transitive
dependencies retain their own licenses. uv.lock records exact distributions.
Distribution of a combined bundle requires collecting the licenses of the actual
files shipped, including MaaCore dependencies and any separately licensed assets.

MaaFramework (LGPL v3) and MaaAI (MIT) are research references only at this stage.
Their root licenses do not establish rights to third-party model weights or game
resources. Do not redistribute those files without recording their provenance.

The P4–P6 native build uses MaaUtils at commit
`f093a19d07db89f2a03bf0ec50c2061a49758783` (LGPL v3; see its `LICENSE.md`) and the
upstream MaaDeps v2.15.1 binary development bundle. They remain external build
dependencies; they are not included in the Python wheel. MaaCore modifications
are maintained as an AGPL-3.0-only patch against the recorded source baseline.
Redistribution of any native runtime must include the actual dependency notices
and corresponding-source/relinking obligations for the files shipped. The P6 distribution job
uploads the Python wheel, source distribution, and per-file provenance inventory as CI artifacts.
The native build job records an ABI report. Users build native binaries from the pinned MaaCore
source and patch series and prepare `battle-v1` from a compatible local MAA installation. The local
resource manifest identifies upstream source paths for each copied or transformed file. Upstream
templates, OCR model weights, battle data, and helper executables remain local.
