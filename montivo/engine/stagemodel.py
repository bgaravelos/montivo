"""Which Spark model a call runs on: the stages measured to be as good on Muse Spark Open (Baseten) as on Spark 1.3.

Measured 1/11/2026 with tools/open_stages_bench.py, tools/monti_bench.py and tools/chat_bench.py (results in
docs/pipeline.md "Open weight stages"). A stage in OPEN_STAGES runs on config.OPEN_MODEL whenever BASETEN_API_KEY is
set, else on Spark 1.3; when the open call fails (an HTTP error, no parsable JSON after the retry) the same prompt
goes to Spark 1.3 once, so a Baseten outage never breaks the feature. Stages left out stay on Spark 1.3: the watch and
the review must see and hear the footage, and the rest scored lower on Spark Open (the chat router 33/38 against
37/38, title hooks, post text, cover headlines; the plan repair follows the planner).
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import OPEN_MODEL, SPARK_MODEL

# stage -> why it runs on Spark Open (shown in the run log and the docs)
OPEN_STAGES: dict[str, str] = {
    "monti": "the help gecko's answer over the guide: 24/24 on monti_bench like Spark 1.3, the 9K-token guide cached on "
             "Baseten ($0.0035 a question against $0.013), 2.9 s against 3.5 s",
    "look": "a caption look from words: 24/24 requests met like Spark 1.3, no answer needed a correction, $0.0034 a "
            "call against $0.0061, 3.1 s against 4.2 s",
}


def open_ready() -> bool:
    return bool(os.environ.get("BASETEN_API_KEY"))


def model_for(stage: str) -> str:
    """The model this stage runs on now: Spark Open when the stage was adopted and the Baseten key is set."""
    if os.environ.get("MONTIVO_OPEN_STAGES", "1") == "0":  # turns every adopted stage back to Spark 1.3
        return SPARK_MODEL
    return OPEN_MODEL if stage in OPEN_STAGES and open_ready() else SPARK_MODEL


def call(which: str, run_dir: Path, ledger, name: str, prompt: str, schema: dict, log=print,
         model: str | None = None, **kw):
    """Spark(run_dir, ledger, model_for(which)).call(name, prompt, schema, **kw) (`which` an OPEN_STAGES key; `stage`
    in kw stays the ledger's stage name); an open-model failure asks Spark 1.3
    the same once (when MODEL_API_KEY is set). `model` forces one model (no fallback). Returns the SparkCall with
    `.model` set to the model that answered."""
    from .spark import Spark
    chosen = model or model_for(which)
    try:
        out = Spark(run_dir, ledger, model=chosen, log=log).call(name, prompt, schema, **kw)
    except (Exception, SystemExit) as e:  # noqa: BLE001 - any open-model failure falls back
        if model or chosen == SPARK_MODEL or not os.environ.get("MODEL_API_KEY"):
            raise
        log(f"spark: {name} on {chosen} failed ({str(e)[:160]}); asking {SPARK_MODEL}")
        chosen = SPARK_MODEL
        out = Spark(run_dir, ledger, model=chosen, log=log).call(name, prompt, schema, **kw)
    out.model = chosen
    return out
