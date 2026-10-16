"""The GGUF converter Export to Ollama runs: llama.cpp's convert_hf_to_gguf.py.

Export folds a job's LoRA adapter into its base model, converts the merged
folder to one GGUF file with this converter, and registers the file with
`ollama create`. Ollama imports GGUF on every platform; an Ollama that takes
safetensors only through its MLX engine cannot import the merged folder itself
(OLLAMA_SAFETENSORS_IMPORT in backend/tasks/training_tasks.py).

A stock install does not carry the converter. Settings > Training libraries
lists it at the llama.cpp commit in PIN; a person's Install click fetches
exactly the files in FILES from raw.githubusercontent.com at that commit,
checks each one's size and sha256 against FILES, and only then moves them into
data/training/gguf_converter. Remove deletes that folder. As with the training
libraries, the routes refuse a request without a one-use plan token from the
modal's status read, and no task, schedule or startup step calls start_install.

Nothing is pip-installed. The converter runs in the backend's Python on the
torch, transformers, numpy and sentencepiece Guaardvark already installs, with
the gguf-py package from the same commit ahead of any installed gguf on its
path. llama.cpp's own requirements file for it pins numpy 2.2 and transformers
4.57, which this backend cannot take (backend/constraints.txt holds numpy below
2); the measurements in ARCHITECTURES and LEVELS were made on the backend's own
versions. It runs offline (training_runner.trainer_env) and on the CPU.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PIN = {
    "name": "GGUF converter",
    "project": "llama.cpp",
    "repo": "ggml-org/llama.cpp",
    "tag": "b11352",
    "commit": "4e2713c1620f1fadb2c3afcc0a8f01500d68ac17",
    "license": "MIT",
    "pinned_because": ("The llama.cpp release of 2026-10-02, two weeks old when pinned. Its "
                       "converter ran on the backend's own torch 2.6, transformers 5.16 and "
                       "numpy 1.26 (ARCHITECTURES)."),
}

SOURCE_URL = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"

# Every file the converter needs at PIN's commit: (path in the llama.cpp tree,
# bytes, sha256). Each sha256 was taken from a download whose git blob hash
# matched the one GitHub's tree lists for the commit. The two LICENSE files
# travel with the code, as the MIT license asks.
FILES: tuple[tuple[str, int, str], ...] = (
    ("convert_hf_to_gguf.py", 13012, "e9a1da876330bbce9687541ab31736542a01b4ac43c6686126514a50f122fb7f"),
    ("LICENSE", 1078, "94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d"),
    ("gguf-py/LICENSE", 1072, "ef78c7e6659e34c798194f0d344e807c722de70cbca1359ef54772661e11ca38"),
    ("conversion/__init__.py", 16149, "69e73ce6e0cb6c2bf895583b5f60e1af0765bc500799420bbe3e369d3c15bec3"),
    ("conversion/afmoe.py", 3229, "d1d91663fd703614b242ddcd6c4f14907068e19539113d5dde7db87258cb46ae"),
    ("conversion/arctic.py", 6867, "0f81fda5dfdcca4859c6955270d2d7c352ffd20c0a64c4fc4f557cb24d40b556"),
    ("conversion/baichuan.py", 2550, "698f48eab3fffb30226ef8ccd69fd4544ca7cac070fb44e50e70e60d235aae1d"),
    ("conversion/bailingmoe.py", 9482, "39c96855c12eef3675640a369e3f6454dddad78486709e2fa3ba60039ff420b9"),
    ("conversion/bailingmoe3.py", 14059, "6a29102ec553d62596a74516df27b26392626f6a525f821b684cd596d10773d0"),
    ("conversion/base.py", 149314, "b517b5e46a5ee403da8fd2f0fa5ac9b4909f3dd1eadf86043c58a16ba9ede65e"),
    ("conversion/bert.py", 27502, "6007377c12d0b05305d380ab6ee3430f97d0a190085758f06d206337002c6638"),
    ("conversion/bitnet.py", 1883, "175b64e46105466314e0382ecafb02ebc73db732b8c582604f20ee4f9ab6caa5"),
    ("conversion/bloom.py", 2983, "cf17a9c8717c45121c843112dfce7a2fd2aeab060158fb6349f50397f4963c1d"),
    ("conversion/chameleon.py", 2436, "96db50d7987eaeb7ac720680577aa1fc97616afc5cb1dbcbf24bd29991a18d34"),
    ("conversion/chatglm.py", 8437, "1a01906ca3265438e269137085b2e46c88c6000d56d1d999ef5f62f5b519c01d"),
    ("conversion/codeshell.py", 1020, "d717e5059b63090878ec1db6fb2e50cd9cdc6980111e9b3e89e89b00e8b95a00"),
    ("conversion/cogvlm.py", 1085, "4d0349fd3276f318a11423a2b6349176dbf3a2dd7bfd76dfe69bae6a26ce22dd"),
    ("conversion/command_r.py", 8193, "d1c20f087af2b0d4b761cbc287cf96d599a8696c23934e520b4b798bd3a812a7"),
    ("conversion/dbrx.py", 3689, "7b28f1384af62bde5e009c0f90f5f0bf26409af447ad6ce91c8511f8eb5b42d4"),
    ("conversion/deci.py", 9382, "1feb845f3430f023f4e9d47777dda5a783eff6c325fba6fb3dce1d1effa1672a"),
    ("conversion/deepseek.py", 51450, "09ba15cefd9448d13f27eb0b46462d1a845d7576a6b401f0bf81d73e0419d655"),
    ("conversion/dots1.py", 1202, "e730b03e8fdff313907876c8e7a56f405a97009b2bbf0c951e6c0362b9696b54"),
    ("conversion/dots3.py", 16919, "e882d5d674f7fb2ac28578e22eee5e7d8b9a9500230ba55271152863d7e195a3"),
    ("conversion/dotsocr.py", 2142, "cdd09c97f594b8c411e6df727c71df51df28c8d722c35bd78fdcdd8ffb108d83"),
    ("conversion/dream.py", 2905, "5f9b431e76f9f0cb112e9648350d0696807e5ffee386d745f3465384a76ff976"),
    ("conversion/ernie.py", 8835, "828db416eef663c28610bf64e6c0d81529558bee16d8fce97d3e54b6157b2519"),
    ("conversion/exaone.py", 15303, "9f3dd7859cf59bcf6e900560954fb094e931d603cd3d8b1300ec664ad778b356"),
    ("conversion/falcon.py", 2749, "57697368f41af0ddca9278d2fde3c1d200f92efe9296aa61c51bbd7d217b6a42"),
    ("conversion/falcon_h1.py", 5506, "950a28acfdf998c0b3c5dca7fc715f342300fd96040f58aa429d5b9d6bbab4d4"),
    ("conversion/gemma.py", 56704, "b127a524d92660c86c67a371996ef29076c94cb8bcb9a8c4facf060a901d1671"),
    ("conversion/glm.py", 27523, "73a874f5296590af413f865b8cb8ecd54cca0a0c3df7a914a4ee7a7ea47d1b0a"),
    ("conversion/gpt2.py", 3319, "b6c57b549c5545fb228b28e863e29a8b449e59d8370f94f0c3c781d40806e03b"),
    ("conversion/gpt_oss.py", 5976, "d53ea4177210de1c9ee96d4ace6a2f46ee4640849dbc273e57fc540584faaeb3"),
    ("conversion/gptneox.py", 2949, "63e06a691f471828e1cd2671b6dd0749953bb347aab525a260ad4c0b2b81e6c2"),
    ("conversion/granite.py", 38552, "917ce34bcdf6089de3e288338485884010e56343db95ad71a526e226958ee23b"),
    ("conversion/grok.py", 5204, "7784cb4ad5860e0ddc9667a4379e0dafbf11700553249d87fb5b9819354b1fd1"),
    ("conversion/grovemoe.py", 4864, "1a20e35f996016fcaf8dfdaa18ee1e1167001ae13e067ac5c0dd45b7e8525e69"),
    ("conversion/hrm_text.py", 3802, "01f6d22c1a626db41d256cac0e43afbab4c80884bf08e8fe2013ba954091da1a"),
    ("conversion/hunyuan.py", 22631, "f437578aa60f0b601e960912bdbc664ebe22c814440063b05c15ad4bb1c3681b"),
    ("conversion/hy_v4.py", 11833, "0958cc54309901322dd951809cc57a1f9ed33a1aa12cb86653e45ee8e18ca7d0"),
    ("conversion/internlm.py", 10991, "ca67d95e16ade174e94ff09bcd0bf6ee7de073d577ee965a69974c2f9ed07ee8"),
    ("conversion/internvl.py", 4523, "275186d2ea58e4504fe643190037bb158ea5390e631de43327bcf00f464d95b0"),
    ("conversion/jais.py", 4180, "752f826b35c2217019c1ce98dfcd42255cac34a0ad8870d31bdc0aa7da588b39"),
    ("conversion/jamba.py", 5055, "1a66ca03190d682a55311c650869299d384227f55cadde7c0d6dc4caa03afbcb"),
    ("conversion/januspro.py", 4390, "0ca502908248bacb54a94df515945d2863a9b3174247bcdf046748b45a203280"),
    ("conversion/kimi_k3.py", 16673, "72c9767d0a7b9482fca9c1945a3e3d5d3bca440ed8f7e89619b2ced515f0f593"),
    ("conversion/kimi_linear.py", 11679, "bc1067ab4deb2326c35a4fcb4e2e8dae7615e65cfdee390d278c5b79f5420b1c"),
    ("conversion/kimivl.py", 7816, "73cb871ea06b4dd81ed1dfc62bf27c11091353085fd4f65ed8077ddb72af66c1"),
    ("conversion/laguna.py", 10564, "b4535ecd0bfb90f70cd4fa9897ba1116e8d7e8c539c42b75bb9f6037eb5bc392"),
    ("conversion/lfm2.py", 10694, "131479370d8d9127d4fe8e7d81453618d80e303488f0343c677596e112761012"),
    ("conversion/lighton_ocr.py", 933, "df7f8030f4469eaa10aac0b70dda8af2a22b2ddfc6af6f41112e03795ed58c77"),
    ("conversion/llada.py", 7125, "7e300a3515192e0ff12637b6911d353834e710b69777252f46eee42f1f0829bd"),
    ("conversion/llama.py", 21362, "6739a507a74d54a0fb884374da57f6f7844495f233cd1a4d9183cf1a15577eb2"),
    ("conversion/llama4.py", 1713, "a39647bb645f2a667effa2998d6be8a71bdbf412f71b8e095b45a7594f20fbbc"),
    ("conversion/llava.py", 6141, "4a331b489410d77319443bfd49862f62984440116d3fd7d36b1ac99ebf100906"),
    ("conversion/maincoder.py", 453, "7ce3968578f4dfd62e6b1639d1ab4aa012b9a3f33b78063a7692be79a682e715"),
    ("conversion/mamba.py", 9739, "8e11ee0c94754f0471762a6770d953c988bbf3744b6734e669a325460492059d"),
    ("conversion/maple.py", 3588, "f82105b61d8479cfd2af2a45107e9340dc78b1ee459ae2bfb50f3b5bfc0eab02"),
    ("conversion/mellum.py", 2550, "5852d10e765e67a703ee8b0a75a94061566dc667ebfa325f74ced82ecc1615dc"),
    ("conversion/mimo.py", 23497, "f0ab957609eb1807d1d9a4c75d4fff26e2c3f82c7e34224499d99aa247a51b75"),
    ("conversion/minicpm.py", 9111, "ef2627437d2649e6615e47ecc92739b0f09ff1ce1e0454d94c639369374d92c9"),
    ("conversion/minimax.py", 12250, "dede6725f2da3c3a24fe8146ca6e6f05d4ddf0ebe6b12786e1d0c33593437425"),
    ("conversion/mistral.py", 9459, "08b157feb91b3f3d62210252313fc0d45482bc397efd13b9411d40ecce146e21"),
    ("conversion/mistral3.py", 2419, "2557b19ac439836ae3deebefe5281b38e8ae4cb204c6f59815288de3e1d250ef"),
    ("conversion/mpt.py", 2098, "07513d08befc56233d5520714876bfcf43fdece0f8c8f934249db8dcfe2bae99"),
    ("conversion/muse_glimmer.py", 8091, "9bc13418128e4d3404898cb40a5dcffb948ba58e2a5c06c0489f44221b5eda0a"),
    ("conversion/nanbeige.py", 837, "a3256dfb8253eaff6f5f16f36ec03bb072392599829404d1c97ef1df59b9db58"),
    ("conversion/nemotron.py", 28808, "d782baa8d4f61cd3e312b0e0d0b0a937fca40a1651212ec5fb47cf9e71ca2d7d"),
    ("conversion/olmo.py", 4521, "d97ad239165f720f7c393374cd04d4d1bf7bba55d27ef2111bfa632ccc7cf6b9"),
    ("conversion/openelm.py", 3724, "bf93fe8be8611a7d5bdc5ee0a1a724db80f69a2045bbab74debb0e99b02cb73c"),
    ("conversion/orion.py", 1718, "1b072a79944a52290b4299b047c0a7f91f7bcb131d332239caa5be86c032802a"),
    ("conversion/pangu.py", 1841, "3c69073f05db63a812735d5a94c8ca75f3eb74916f61ed1b3c72c9752bae408e"),
    ("conversion/phi.py", 18221, "c8b05d9c91db3a60496b38ffdc2e085e67dec226d15047ab7b1fcf9c0890498c"),
    ("conversion/pixtral.py", 1408, "ad39d0eb893e05aaa025d520d92834c6645c186398553ffb396ea5fec079a0fd"),
    ("conversion/plamo.py", 9392, "b5dc6817a560950c92eb7a39df0e5a24647155e0b26e70857fa200702bbba8d5"),
    ("conversion/plm.py", 835, "f11eeb8836b675dd26d2f8bb429860821f59fd202a1a36dee1d07179f9b0423e"),
    ("conversion/pockettts.py", 18948, "638461f1fde1a147e0fa48194e2a643bd20f801c82718a61795ba84cea8074c7"),
    ("conversion/qwen.py", 45338, "6d469451973d4df71512116ad2c19ec902e54fbafcaa27b848a7149e3275ab10"),
    ("conversion/qwen3tts.py", 25608, "8919e102f0b8407a39e94e3e93a5033597066d54a48e327d5c5716f066edb3f1"),
    ("conversion/qwen3vl.py", 18179, "b6ff12642f41a7f6224a77b4c2d2b47d11b11d807671a0a1c1ab692419e0b67f"),
    ("conversion/qwen4exp.py", 10101, "a955f632a01068fc0d311325152a37807c290e283798ba6ab0a57e8347bed2fa"),
    ("conversion/qwenvl.py", 9152, "ec8fb850a2ebf19191893a861f18f9db4cfc23ff059543036b7227e5feda73ea"),
    ("conversion/refact.py", 3274, "af232c6d49ab59e507d5bf672fb34780ff1e1b753402eefb68789d986d94822b"),
    ("conversion/rwkv.py", 14442, "0d9c40c1966c5eff5f90c088c42bd7e36ad0a9d789d558b9069d0209743a057e"),
    ("conversion/sarashina2.py", 1074, "fd2a42fbe127c4e6c20afa1640f8e9736a8d825e301e8bc12a156cb303a0c0a0"),
    ("conversion/smallthinker.py", 3517, "e976485465e39adcfd794764111d5c51bf051cdc7aca888c4790938c2ddb7636"),
    ("conversion/smolvlm.py", 2153, "4f069f17c96876ca940fa027fe7a79d05bcb3d18b47694d78d497b3aa8516f53"),
    ("conversion/spark2_5.py", 2876, "dfe5873de23884e1686619d6743cfaeca3368ce4687f055f0ad5a23549b89e40"),
    ("conversion/stablelm.py", 4024, "d5d382d98299484f3dc5308419bfd67c49cf44e55431f98ea7e68b8392112685"),
    ("conversion/starcoder.py", 982, "d6b83fba5ad11f03d2f904f44ac0e0bac122ef5a392be446bc5dd8ca2ebdc14e"),
    ("conversion/step3.py", 16724, "594ae4b97df840ea6790d6cba3710f4454f38675de6c9d064102a23e5e3f62b4"),
    ("conversion/t5.py", 14273, "0e28b2ae4d9db47e101a78335bbaf6b067273cd4368da03f92f0c5a805dc780b"),
    ("conversion/talkie.py", 2138, "ab06701e8ccba86cae232e8f377d9d877dfe00a84288ec641ae73886241bd48c"),
    ("conversion/ultravox.py", 9169, "d23d99493170316f3dae9e1af7a9b5d18a8b7b3c987ccb83280091666c84df38"),
    ("conversion/wavtokenizer.py", 1772, "cc6c6fd91cb17070593b37175624c769e904bde584b3e7024906809cfb25a1d8"),
    ("conversion/xverse.py", 3909, "c14955d02a33f040d56cbca197de1ac026cf9a74b62f94a376f3c3dd4d866337"),
    ("conversion/youtuvl.py", 2931, "e93fb9ebeff14d11efcfeac22bfd408921a67a8b57f12b499d11cd2c368be60e"),
    ("gguf-py/gguf/__init__.py", 219, "3ccfc0104cd7ea88c6028743b7bf3f2c89b5f474425de03a217a6072320d7c2f"),
    ("gguf-py/gguf/constants.py", 237584, "8defce15ed199e68fe04e72cf05527babbf7ed74788b739eceef8038dfa30517"),
    ("gguf-py/gguf/gguf.py", 478, "f0c0eeedad0911784b52ffed8e162a0eb5ae6d535ce35705bc16196e46597a72"),
    ("gguf-py/gguf/gguf_reader.py", 16321, "d0ea743200e19d7ef0a4c969edcc4a6dba8e3656a790b217e3bdc7b31170718e"),
    ("gguf-py/gguf/gguf_writer.py", 71904, "ae45f02b8522e00e054fdc6855cc368068c966a510d05cbcbb6a063620b31b22"),
    ("gguf-py/gguf/lazy.py", 12015, "dbc98e3ee9ef8606df34e9d91f98ab29c2697e6824995f1dd8952938c135ad85"),
    ("gguf-py/gguf/metadata.py", 39318, "7cedac3b8457271a3f58e5531a7e6958fdecb9ea072f7895ba5d7f6693c9db29"),
    ("gguf-py/gguf/py.typed", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("gguf-py/gguf/quants.py", 64946, "2c927a1b3d9f0920dcf4007fb686e1b0999333e9f65ce43dcc689900c0beae8b"),
    ("gguf-py/gguf/tensor_mapping.py", 135238, "50a0bbe173a41bb8a2edac18556c9a5be0bc1b23dd8621f559e8d966bb78eca1"),
    ("gguf-py/gguf/utility.py", 13534, "da920e2c62166ec9f30e407e9fb35ec29fda671310ffbf0f8ce856b62f1c70d7"),
    ("gguf-py/gguf/vocab.py", 42992, "353c35e8a52c2afdd113a7a8ce0ddade6cf9b5bb9cf150f965269ea41af3e5fd"),
)

DOWNLOAD_BYTES = sum(size for _, size, _ in FILES)

# Base-model families (config.json "model_type", which each
# training_base_models entry declares as "architecture") that this pin has
# converted here into a model Ollama ran. Export refuses any other family
# before it merges; a family is added only after the same check.
ARCHITECTURES = {
    "qwen2": ("2026-10-16, Linux, NVIDIA 16 GB, Ollama 0.40.1: Qwen2.5-1.5B-Instruct with a "
              "rank-16 LoRA adapter merged (7 s), converted to Q8_0 and to F16, each imported "
              "with `ollama create` and asked \"Say hello in five words.\"; both answered "
              "\"Hello! How can I help you today?\" and stopped at <|im_end|>."),
}

# What Export offers through GGUF: the converter's own output types.
# size_share is the file's size against the bf16 weights, measured on the
# Qwen2.5 1.5B export above. Nothing smaller is offered: Ollama 0.40.1 refuses
# --quantize for a GGUF import ("create-time quantization is only supported for
# safetensors imports"), and the K-quants (q4_K_M and the like) need llama.cpp's
# compiled llama-quantize, which this install does not fetch.
LEVELS = {
    "q8_0": {"outtype": "q8_0", "label": "Q8_0", "size_share": 0.53,
             "measured": ("1,646,572,544 bytes from 3.09 GB of bf16 weights, converted in about "
                          "9 s on the CPU; Ollama ran it on the GPU in 1.8 GB")},
    "f16": {"outtype": "f16", "label": "F16", "size_share": 1.0,
            "measured": ("3,093,668,864 bytes, converted in about 7 s on the CPU; Ollama ran it "
                         "on the GPU in 3.3 GB")},
}
DEFAULT_LEVEL = "q8_0"

NEEDS_CLICK = ("The GGUF converter installs and is removed only from its buttons in "
               "Settings > Training libraries. Open it there and click the button.")

_RECORD_NAME = "guaardvark-install.json"
_FETCH_TIMEOUT = (15, 60)
_VERIFIED_FOR_SECONDS = 30.0
CONVERT_TIME_LIMIT_SECONDS = 3600


class Refused(Exception):
    """A request the routes answer with an error, never with a download."""

    def __init__(self, message: str, status: int = 400, code: str = "REFUSED"):
        super().__init__(message)
        self.status = status
        self.code = code


class ConverterFailed(RuntimeError):
    """The converter ran and did not produce the GGUF file; the message says why."""


_lock = threading.Lock()
_run: dict[str, Any] = {}
_verified: dict[str, Any] = {"key": None, "at": 0.0, "state": None}


def _idle_run() -> dict[str, Any]:
    return {"state": "idle", "action": None, "progress": 0, "error": None,
            "started_at": None, "finished_at": None, "fetched_bytes": 0}


_run.update(_idle_run())


# ---- where it lives and what is there ------------------------------------------

def install_dir() -> Path:
    from backend.config import STORAGE_DIR
    return Path(STORAGE_DIR) / "training" / "gguf_converter"


def script_path() -> Path:
    return install_dir() / "convert_hf_to_gguf.py"


def _record_key(record: Path):
    try:
        st = record.stat()
    except OSError:
        return None
    return (str(record), st.st_mtime_ns, st.st_size)


def _check_files(folder: Path) -> str | None:
    """The first pinned file that is missing or differs, in words, or None."""
    for path, size, digest in FILES:
        try:
            data = (folder / path).read_bytes()
        except OSError:
            return f"{path} is missing"
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            return f"{path} does not match its pinned sha256"
    return None


def installed_state(*, fresh: bool = False) -> dict[str, Any]:
    """{"state": "missing" | "installed" | "other_version" | "damaged", "tag",
    "problem"}. Installed means the record names PIN's commit and every file
    in FILES matches its sha256; a check is reused for 30 s unless fresh."""
    folder = install_dir()
    record = folder / _RECORD_NAME
    key = _record_key(record)
    if key is None:
        return {"state": "missing", "tag": None, "problem": None}
    with _lock:
        if (not fresh and _verified["key"] == key
                and time.monotonic() - _verified["at"] < _VERIFIED_FOR_SECONDS):
            return dict(_verified["state"])
    try:
        meta = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if meta.get("commit") != PIN["commit"]:
        state = {"state": "other_version", "tag": meta.get("tag"),
                 "problem": f"llama.cpp {meta.get('tag') or 'of another version'} is installed"}
    else:
        problem = _check_files(folder)
        state = {"state": "damaged" if problem else "installed", "tag": meta.get("tag"),
                 "problem": problem}
    with _lock:
        _verified.update(key=key, at=time.monotonic(), state=dict(state))
    return state


def _forget_check() -> None:
    with _lock:
        _verified.update(key=None, at=0.0, state=None)


# ---- what Export may do -----------------------------------------------------------

def levels() -> list[dict[str, Any]]:
    """The levels Export offers, default first, as the export form reads them."""
    order = [DEFAULT_LEVEL] + [k for k in LEVELS if k != DEFAULT_LEVEL]
    return [{"value": k, "label": LEVELS[k]["label"], "size_share": LEVELS[k]["size_share"],
             "default": k == DEFAULT_LEVEL} for k in order]


def level_for(quantization: Any) -> str:
    """The LEVELS key for a requested level; None means DEFAULT_LEVEL. Raises
    ValueError naming the choices for any other."""
    if quantization is None or quantization == "":
        return DEFAULT_LEVEL
    key = str(quantization).strip().lower()
    if key in LEVELS:
        return key
    choices = ", ".join(LEVELS[k]["label"] for k in LEVELS)
    raise ValueError(f"Export through GGUF offers {choices}; {quantization} is not one of them.")


def refusal_for(entry: dict[str, Any] | None) -> tuple[str, str] | None:
    """(code, reason) when Export cannot convert this base model now, or None."""
    with _lock:
        busy = _run["state"] == "running"
    if busy:
        return ("GGUF_CONVERTER_BUSY",
                "The GGUF converter is being installed. Export once Settings > Training "
                "libraries shows it installed.")
    state = installed_state()
    if state["state"] == "missing":
        return ("GGUF_CONVERTER_MISSING",
                f"Export to Ollama converts the trained model to GGUF with llama.cpp's converter, "
                f"which is not installed. Install it in Settings > Training libraries > GGUF "
                f"converter ({_size_label(DOWNLOAD_BYTES)} from GitHub).")
    if state["state"] != "installed":
        return ("GGUF_CONVERTER_NOT_READY",
                f"The GGUF converter cannot be used: {state['problem']}. Install it again in "
                f"Settings > Training libraries > GGUF converter.")
    family = (entry or {}).get("architecture")
    if family not in ARCHITECTURES:
        name = (entry or {}).get("name") or "this base model"
        return ("GGUF_FAMILY_UNCHECKED",
                f"The GGUF converter has not been checked with {name} ({family or 'unknown'} "
                f"models) yet, so Export does not offer it.")
    return None


def _size_label(n: int) -> str:
    return f"about {n / 1_000_000:.1f} MB"


# ---- status ------------------------------------------------------------------------

def _run_snapshot() -> dict[str, Any]:
    with _lock:
        return dict(_run)


def status(*, with_plan_token: bool = False) -> dict[str, Any]:
    state = installed_state()
    result = {
        "name": PIN["name"], "project": PIN["project"], "repo": PIN["repo"],
        "tag": PIN["tag"], "commit": PIN["commit"], "license": PIN["license"],
        "pinned_because": PIN["pinned_because"],
        "source": f"https://github.com/{PIN['repo']}/tree/{PIN['commit']}",
        "download_host": "raw.githubusercontent.com",
        "files": len(FILES), "download_bytes": DOWNLOAD_BYTES,
        "role": ("Converts a fine-tuned model to one GGUF file, which Export registers "
                 "with Ollama."),
        "python_packages": ("None. It runs on the torch, transformers and numpy Guaardvark "
                            "already has."),
        "location": str(install_dir()),
        "state": state["state"], "installed_tag": state["tag"], "problem": state["problem"],
        "architectures": sorted(ARCHITECTURES),
        "levels": levels(),
        "run": _run_snapshot(),
    }
    if with_plan_token:
        from backend.services import training_libraries
        result["plan_token"] = training_libraries.issue_plan_token()
    return result


# ---- install and remove --------------------------------------------------------

def _take_token(token: Any) -> bool:
    from backend.services import training_libraries
    return training_libraries.take_plan_token(token)


def _spawn(target, *args, name: str) -> None:
    threading.Thread(target=target, args=args, daemon=True, name=name).start()


def start_install(token: Any) -> dict[str, Any]:
    """Fetch and check the pinned files in a background thread."""
    if not _take_token(token):
        raise Refused(NEEDS_CLICK, 403, "NEEDS_CLICK")
    if installed_state(fresh=True)["state"] == "installed":
        raise Refused(f"The GGUF converter (llama.cpp {PIN['tag']}) is already installed.",
                      409, "ALREADY_INSTALLED")
    with _lock:
        if _run["state"] == "running":
            raise Refused("The GGUF converter is already being installed.", 409, "BUSY")
        _run.update(_idle_run())
        _run.update({"state": "running", "action": "install", "progress": 1,
                     "started_at": time.time()})
    _spawn(_install, name="gguf-converter-install")
    return status()


def start_remove(token: Any) -> dict[str, Any]:
    """Delete the installed converter."""
    if not _take_token(token):
        raise Refused(NEEDS_CLICK, 403, "NEEDS_CLICK")
    with _lock:
        if _run["state"] == "running":
            raise Refused("The GGUF converter is being installed; wait for it to finish.", 409, "BUSY")
    folder = install_dir()
    if not folder.exists():
        raise Refused("The GGUF converter is not installed; there is nothing to remove.", 409,
                      "NOTHING_TO_REMOVE")
    shutil.rmtree(folder)
    _forget_check()
    with _lock:
        _run.update(_idle_run())
    logger.info("GGUF converter removed from %s", folder)
    return status()


def _finish(state: str, error: str | None = None) -> None:
    with _lock:
        _run.update({"state": state, "error": error, "finished_at": time.time(),
                     "progress": 100 if state == "completed" else _run["progress"]})


def _fetch(session, path: str, size: int) -> bytes:
    """One pinned file's bytes; never reads past one byte more than FILES says."""
    url = SOURCE_URL.format(repo=PIN["repo"], commit=PIN["commit"], path=path)
    chunks, got = [], 0
    with session.get(url, timeout=_FETCH_TIMEOUT, stream=True) as response:
        if response.status_code != 200:
            raise ConverterFailed(f"GitHub answered {response.status_code} for {path}; "
                                  f"nothing was installed.")
        for chunk in response.iter_content(chunk_size=65536):
            got += len(chunk)
            chunks.append(chunk)
            if got > size:
                break
    return b"".join(chunks)


def _install() -> None:
    import requests

    target = install_dir()
    staging = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".gguf_converter-", dir=target.parent))
        with requests.Session() as session:
            for i, (path, size, digest) in enumerate(FILES):
                data = _fetch(session, path, size)
                if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                    raise ConverterFailed(f"{path} from GitHub does not match its pinned sha256; "
                                          f"nothing was installed.")
                dest = staging / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                with _lock:
                    _run["fetched_bytes"] += size
                    _run["progress"] = max(_run["progress"], 1 + int(97 * (i + 1) / len(FILES)))
        (staging / _RECORD_NAME).write_text(json.dumps({
            "tag": PIN["tag"], "commit": PIN["commit"], "files": len(FILES),
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }, indent=2), encoding="utf-8")
        old = None
        if target.exists():
            old = target.with_name(f".gguf_converter-old-{os.getpid()}-{int(time.time())}")
            target.rename(old)
        staging.rename(target)
        staging = None
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        _forget_check()
        _finish("completed")
        logger.info("GGUF converter installed: llama.cpp %s into %s", PIN["tag"], target)
    except ConverterFailed as e:
        _finish("failed", str(e))
    except Exception as e:
        logger.exception("GGUF converter install failed")
        _finish("failed", f"The download failed ({type(e).__name__}: {e}); nothing was installed. "
                          f"Check the network and click Install again.")
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


# ---- converting --------------------------------------------------------------------

def converter_env() -> dict[str, str]:
    """The trainer's offline environment, with the GPU hidden: the converter
    reads and writes tensors on the CPU only."""
    from backend.services import training_runner
    env = training_runner.trainer_env()
    env["CUDA_VISIBLE_DEVICES"] = ""
    return env


def convert_command(merged_dir: Path, out_file: Path, level: str) -> list[str]:
    return [sys.executable, str(script_path()), str(merged_dir),
            "--outtype", LEVELS[level]["outtype"], "--outfile", str(out_file)]


def convert(merged_dir: Path, out_file: Path, level: str, *, log_path: Path,
            time_limit_s: float = CONVERT_TIME_LIMIT_SECONDS) -> Path:
    """Convert a merged Hugging Face folder to out_file (GGUF at `level`).

    The installed files are checked against FILES first. The converter writes
    beside out_file and the result is renamed into place only when it exits
    cleanly, so a failed run never leaves a partial file under the final
    name. Its output is appended to log_path. Raises ConverterFailed."""
    level = level_for(level)
    state = installed_state(fresh=True)
    if state["state"] != "installed":
        raise ConverterFailed(f"The GGUF converter cannot be used: "
                              f"{state['problem'] or 'it is not installed'}.")
    out_file = Path(out_file)
    partial = out_file.with_name(out_file.name + ".partial")
    partial.unlink(missing_ok=True)
    command = convert_command(Path(merged_dir), partial, level)
    tail: list[str] = []
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} convert to GGUF ({level}) ===\n")
        log.flush()
        try:
            proc = subprocess.run(command, cwd=str(out_file.parent), env=converter_env(),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  timeout=time_limit_s)
        except subprocess.TimeoutExpired:
            partial.unlink(missing_ok=True)
            raise ConverterFailed(f"The GGUF converter passed its time limit of "
                                  f"{time_limit_s / 60:.0f} minutes and was stopped.")
        log.write(proc.stdout or "")
        tail = [line for line in (proc.stdout or "").splitlines() if line.strip()][-1:]
    if proc.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        raise ConverterFailed(f"The GGUF converter exited with code {proc.returncode}: "
                              f"{tail[0] if tail else 'no output'} (full output in {log_path}).")
    partial.replace(out_file)
    return out_file
