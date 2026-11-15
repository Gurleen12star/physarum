"""Answer-level verifiers. The graph only ever learns from one bit per answer.

* NoisyOracle  -- ground truth flipped with probability `noise` (for controlled experiments).
* LLMJudge     -- a real LLM checks every fact on the answer's path ("Is it currently true that
                  Canada's head of state is Charles III?"). Verdicts are cached on disk, so a
                  re-run costs nothing and numbers are reproducible. Its error rate is measured
                  against CoDEx's human labels by scripts/measure_verifier.py.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "verdict_cache.json"


class NoisyOracle:
    name = "simulated (10% noise)"

    def __init__(self, noise: float = 0.1, seed: int = 0):
        self.noise = noise
        self.rng = random.Random(seed)
        self.name = f"simulated ({int(noise * 100)}% noise)"

    def __call__(self, truth: set, answer: int, **_) -> bool:
        ok = answer in truth
        return (not ok) if self.rng.random() < self.noise else ok


PROVIDERS = {
    # env var: (provider, OpenAI-compatible base URL or None, default model, seconds between background calls)
    "ANTHROPIC_API_KEY": ("anthropic", None, "claude-haiku-4-5", 0.3),
    "OPENAI_API_KEY": ("openai", "https://api.openai.com/v1", "gpt-4o-mini", 0.3),
    "GROQ_API_KEY": ("groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", 2.5),  # free tier
    "GEMINI_API_KEY": ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash", 6.5),
}


class LLMJudge:
    """Judges each fact on the answer's path with an LLM ("As of <the visitor's world year>, was this fact true?").

    Providers (first key found): ANTHROPIC_API_KEY, OPENAI_API_KEY (OPENAI_BASE_URL for any compatible
    server), GROQ_API_KEY or GEMINI_API_KEY (both have free tiers). PHYSARUM_JUDGE_MODEL overrides the model.

    Free tiers allow only a few requests per minute, so verdicts on a fast agent stream use cached
    judgements; uncached facts are queued and judged in the background at the provider's pace, and that
    verdict falls back to the simulated judge meanwhile. Votes asked for explicitly are judged live.
    Every fallback is counted and shown, never hidden.
    """

    def __init__(self, fallback=None):
        self.lock = threading.Lock()
        self.cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        env = next(k for k in PROVIDERS if os.environ.get(k))
        self.provider, base, model, interval = PROVIDERS[env]
        self.key = os.environ[env]
        self.base = os.environ.get("OPENAI_BASE_URL", base) if self.provider == "openai" else base
        self.model = os.environ.get("PHYSARUM_JUDGE_MODEL", model)
        self.interval = float(os.environ.get("PHYSARUM_JUDGE_INTERVAL", interval))
        self.fallback = fallback or NoisyOracle(float(os.environ.get("PHYSARUM_NOISE", "0.1")), 1)
        self.calls = self.errors = self.llm_verdicts = self.fallback_verdicts = 0
        self.queue: list = []
        self.queued: set = set()
        self.wake = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def name(self):
        share = self.llm_verdicts / max(1, self.llm_verdicts + self.fallback_verdicts)
        return f"AI judge ({self.model}) · {len(self.cache):,} facts checked · {share:.0%} of verdicts"

    @staticmethod
    def available() -> bool:
        return any(os.environ.get(k) for k in PROVIDERS)

    def _ask(self, prompt: str) -> str:
        if self.provider == "anthropic":
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                method="POST",
                data=json.dumps(
                    {"model": self.model, "max_tokens": 5, "messages": [{"role": "user", "content": prompt}]}
                ).encode(),
                headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["content"][0]["text"]
        req = urllib.request.Request(
            self.base.rstrip("/") + "/chat/completions",
            method="POST",
            data=json.dumps(
                {
                    "model": self.model,
                    "max_tokens": 5,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ).encode(),
            headers={"Authorization": f"Bearer {self.key}", "content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"] or ""

    def _judge(self, f) -> bool | None:
        """f = (as_of_year, subject, relation, object)."""
        prompt = (
            f"As of {f[0]}, was this fact true?\n{f[1]} — {f[2]} — {f[3]}\nAnswer with exactly one word: YES or NO."
        )
        try:
            ok = self._ask(prompt).strip().upper().startswith("Y")
        except Exception:
            with self.lock:
                self.errors += 1
            return None  # never cached: retried later
        with self.lock:
            self.cache["|".join(f)] = ok
            self.calls += 1
            if self.calls % 20 == 0:
                self.save()
        return ok

    def _worker(self):
        while True:
            self.wake.wait()
            while True:
                with self.lock:
                    if not self.queue:
                        self.wake.clear()
                        break
                    f = self.queue.pop(0)
                    self.queued.discard(f)
                if "|".join(f) not in self.cache:
                    self._judge(f)
                    time.sleep(self.interval)

    def save(self):
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(self.cache, indent=0))

    def __call__(self, truth: set, answer: int, path_facts=None, live: bool = False, as_of: int = 2026, **_) -> bool:
        """path_facts: [(subj, rel_label, obj), ...] for the chosen path, judged as of the session's world year."""
        facts = [(str(as_of), *map(str, f)) for f in (path_facts or [])]
        verdicts = []
        for f in facts:
            v = self.cache.get("|".join(f))
            if v is None and live:
                v = self._judge(f)
            if v is None:
                with self.lock:
                    if f not in self.queued and len(self.queue) < 5000:
                        self.queue.append(f)
                        self.queued.add(f)
                self.wake.set()
            verdicts.append(v)
        if facts and all(v is not None for v in verdicts):
            self.llm_verdicts += 1
            return all(verdicts)
        self.fallback_verdicts += 1
        return self.fallback(truth, answer)


def make_verifier(seed: int = 1):
    if LLMJudge.available() and os.environ.get("PHYSARUM_VERIFIER", "llm") == "llm":
        return LLMJudge()
    return NoisyOracle(float(os.environ.get("PHYSARUM_NOISE", "0.1")), seed)
