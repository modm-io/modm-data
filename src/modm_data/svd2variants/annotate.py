# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Naming Features with a Language Model

The heuristic in `modm_data.svd2variants.naming` can only name a feature after
what its elements share lexically, which misses the semantics: the seventeen
bit fields of the first capture/compare channel of a timer share no name and no
description, but they are obviously "capture/compare channel 1".

This module asks a small language model to name them instead and stores the
answers in a lookup table. The table is keyed by the *content* of the feature,
so it is stable across runs, reviewable as a diff, and only features that
actually changed need to be asked again.

Names come from the first of these that works, so that a build never depends on
a model being there:

1. the lookup table of a previous run, which the homepage and the CI download,
2. a local model served by LM Studio, see `local_endpoint`, or the `claude`
   command line tool,
3. the heuristic of `modm_data.svd2variants.naming`.

The prompt below is the result of measuring variants against each other on 91
features of 12 peripheral groups. Keeping the numbers of similar elements as a
range and asking for a different name per feature both helped, while naming the
vague words to avoid produced more of them, and asking the model to first
explain what the elements have in common made the names worse.
"""

import re
import json
import logging
import subprocess
import urllib.request
from hashlib import sha1
from pathlib import Path
from multiprocessing.pool import ThreadPool

from .naming import Element

LOGGER = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
"""The model asked through the `claude` command line tool."""
LOCAL_MODEL = "gemma-4-26b-a4b-it-qat-mlx"
"""The model asked through a local server, which LM Studio loads on demand."""
LOCAL_ENDPOINT = "http://localhost:1234/v1"
"""Where LM Studio serves its OpenAI compatible API by default."""
_MAX_ELEMENTS = 24
_MAX_FEATURES = 16

_INSTRUCTIONS = """\
You name the optional features of STM32 peripherals. Each block below lists registers and bit \
fields of the STM32 {group} peripheral that exist on exactly the same set of devices, so together \
they form one optional feature.

Name each feature with a short noun phrase:
- 2 to 6 words in sentence case, without a trailing period.
- Say what the feature does, not which register it is in. Never use the word "register".
- Summarize many similar elements instead of listing them, but keep their numbers as a
  range, e.g. "Lines 28 to 30 interrupt masking" or "Channels 3 and 4 DMA requests".
- Consider all elements, not only the first ones.
- Do not repeat the peripheral name {group}.
- Give every feature in the reply a different name, since they are different features.

For example, these features of other peripherals:

## a  (5 elements in 4 registers)
  CCMR1.CC1SEL — Capture/compare 1 selection
  CCMR1.IC1PSC — Input capture 1 prescaler
  CCR1.CCR1 — Capture/compare 1 value
  DIER.CC1IE — Capture/compare 1 interrupt enable
  SR.CC1IF — Capture/compare 1 interrupt flag

## b  (5 elements in 4 registers)
  CR.WUTE — Wakeup timer enable
  CR.WUTIE — Wakeup timer interrupt enable
  WUTR.WUT — Wakeup auto-reload value
  SR.WUTF — Wakeup timer flag
  SCR.CWUTF — Clear wakeup timer flag

## c  (5 elements in 2 registers)
  CR1.SMBHEN — SMBus host address enable
  CR1.SMBDEN — SMBus device default address enable
  CR1.ALERTEN — SMBus alert enable
  CR1.PECEN — PEC enable
  TIMEOUTR.TIMEOUTA — Bus timeout A

## d  (18 elements in 3 registers)
  RXF0C.F0SA — Rx FIFO 0 start address
  RXF0S.F0FL — Rx FIFO 0 fill level
  IE.RF0NE — Rx FIFO 0 new message interrupt enable
  RXF0C.F0S — Rx FIFO 0 size
  ... and 14 more similar

## e  (2 elements in 2 registers)
  POL.POL — Programmable polynomial
  CR.POLYSIZE — Polynomial size

are named:
{{"a": "Capture/compare channel 1", "b": "Wakeup timer", "c": "SMBus support", "d": "Receive FIFO 0", \
"e": "Programmable polynomial"}}

Reply with ONLY a JSON object mapping each id below to its name.
"""


def feature_key(elements: list[Element]) -> str:
    """
    :param elements: the elements of one feature.
    :return: a stable key for the content of the feature, so that a table entry
             survives everything except a change to the feature itself.
    """
    lines = sorted(f"{register}.{name}\t{description}" for _kind, register, name, description in elements)
    return sha1("\n".join(lines).encode()).hexdigest()[:16]


def _sample(elements: list[Element]) -> list[Element]:
    """
    Takes elements from all registers in turn, since small models tend to name a
    feature after the first few elements, which are all in the same register.
    """
    registers = {}
    for element in elements:
        registers.setdefault(element[1], []).append(element)
    sample = []
    while len(sample) < min(_MAX_ELEMENTS, len(elements)):
        for queue in registers.values():
            if queue and len(sample) < _MAX_ELEMENTS:
                sample.append(queue.pop(0))
    return sample


def _block(index: int, elements: list[Element]) -> str:
    registers = len({element[1] for element in elements})
    lines = [f"## {index}  ({len(elements)} elements in {registers} registers)"]
    for _kind, register, name, description in _sample(elements):
        lines.append(f"  {register}.{name}" + (f" — {description[:70]}" if description else ""))
    if len(elements) > _MAX_ELEMENTS:
        lines.append(f"  ... and {len(elements) - _MAX_ELEMENTS} more similar")
    return "\n".join(lines)


def _normalize(name: str) -> str | None:
    """
    Brings a generated name into sentence case without a register suffix.

    :return: the name, or `None` if it is not a short noun phrase.
    """
    name = " ".join(name.split()).strip(" .\"'")
    name = re.sub(r"\s+registers?$", "", name, flags=re.IGNORECASE)
    words = name.split(" ")
    if not name or len(words) > 8 or len(name) > 70:
        return None

    # Keep acronyms and mixed case words like IrDA, only lower title case words
    def lower(word: str) -> str:
        return "-".join(part.lower() if part[:1].isupper() and part[1:].islower() else part for part in word.split("-"))

    return " ".join([words[0][:1].upper() + words[0][1:]] + [lower(word) for word in words[1:]])


def local_endpoint(endpoint: str = None, model: str = LOCAL_MODEL, timeout: float = 2) -> str | None:
    """
    Looks for a local server that can name features, so that the names are only
    generated where a model is actually available, see `modm_data.svd2variants`.

    :param endpoint: the URL to check, or the LM Studio default.
    :param model: the model that the server must offer.
    :return: the URL of the server, or `None` if it does not answer or lacks the model.
    """
    url = endpoint or LOCAL_ENDPOINT
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=timeout) as response:
            models = {served.get("id") for served in json.loads(response.read()).get("data", [])}
    except (OSError, ValueError):
        return None
    if model in models:
        return url
    LOGGER.info(f"{url} does not serve {model}, only {sorted(models)}")
    return None


def _claude(prompt: str, model: str) -> str:
    """Asks the model with the `claude` command line tool."""
    result = subprocess.run(
        ["claude", "-p", "--model", model, "--allowed-tools", ""],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode:
        raise OSError(f"claude exited with {result.returncode}: {result.stderr[:200]}")
    return result.stdout


def _openai(prompt: str, model: str, endpoint: str, reasoning: str = "none") -> str:
    """
    Asks the model of an OpenAI compatible server, for example LM Studio at
    `http://localhost:1234/v1`, Ollama or the llama.cpp server.

    :param reasoning: the reasoning effort. Reasoning models like Qwen3.5 think
                      for hundreds of tokens by default even for a short name,
                      and LM Studio only turns that off with `none`.
    """
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                # Names should not change between runs
                "temperature": 0,
                "max_tokens": 2048,
                **({"reasoning_effort": reasoning} if reasoning else {}),
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        answer = json.loads(response.read())
    return answer["choices"][0]["message"]["content"] or ""


def _ask(
    group: str, batch: list[tuple[str, list[Element]]], model: str, endpoint: str = None, retry: bool = True
) -> dict[str, str]:
    """Asks the model to name one batch of features of one peripheral group."""
    prompt = _INSTRUCTIONS.format(group=group) + "\n"
    prompt += "\n\n".join(_block(index, elements) for index, (_key, elements) in enumerate(batch))
    try:
        text = _openai(prompt, model, endpoint) if endpoint else _claude(prompt, model)
    except (OSError, subprocess.TimeoutExpired, KeyError, IndexError, json.JSONDecodeError) as error:
        LOGGER.warning(f"{group}: {error}")
        return {}

    # Reasoning models may think out loud before answering
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if (start := text.find("{")) < 0 or (end := text.rfind("}")) < 0:
        LOGGER.warning(f"{group}: no JSON in the answer: {text[:120]}")
        return {}
    try:
        answers = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        LOGGER.warning(f"{group}: {error}")
        return {}

    names, missing = {}, []
    for index, (key, elements) in enumerate(batch):
        answer = answers.get(str(index)) or answers.get(index)
        if isinstance(answer, str) and (name := _normalize(answer)):
            names[key] = name
        else:
            missing.append((key, elements))
            if not retry:
                LOGGER.warning(f"{group}: no usable name for feature {index}: {answer!r}")
    # Small models sometimes skip a few ids of a batch, which usually works on their own
    if missing and retry:
        names.update(_ask(group, missing, model, endpoint, retry=False))
    return names


def _batches(jobs: dict[str, list[tuple[str, list[Element]]]]) -> list[tuple[str, list]]:
    batches = []
    for group, features in jobs.items():
        for start in range(0, len(features), _MAX_FEATURES):
            batches.append((group, features[start : start + _MAX_FEATURES]))
    return batches


def generate_names(
    jobs: dict[str, list[tuple[str, list[Element]]]],
    model: str = MODEL,
    workers: int = 6,
    progress=None,
    endpoint: str = None,
) -> dict[str, str]:
    """
    Asks the model to name every feature it is given.

    :param jobs: the features to name, as (key, elements) per peripheral group.
    :param model: the model to ask.
    :param workers: how many requests to run at the same time.
    :param endpoint: the URL of an OpenAI compatible server instead of the
                     `claude` command line tool, e.g. `http://localhost:1234/v1`.
    :param progress: called with the names of every finished batch, so that an
                     interrupted run, for example by a usage limit, keeps them.
    :return: the generated names by feature key.
    """
    batches = _batches(jobs)
    names = {}
    if not batches:
        return names
    with ThreadPool(workers) as pool:
        results = pool.imap_unordered(lambda job: _ask(job[0], job[1], model, endpoint), batches)
        for done, result in enumerate(results, 1):
            names.update(result)
            if progress is not None:
                progress(result, done, len(batches))
    return names


def read_names(path: Path) -> dict[str, str]:
    """:return: the lookup table of feature names, or an empty one."""
    if path is None or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text())


def write_names(names: dict[str, str], path: Path):
    """Writes the lookup table sorted by key, so that it diffs cleanly."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(dict(sorted(names.items())), indent=0, ensure_ascii=False) + "\n")
