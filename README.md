# Vera — a clinical voice agent for post-surgical follow-up

Vera calls a recently discharged patient, talks with them by voice in Colombian
Spanish, answers **only** from that patient's clinical documents — citing which
document backs each answer — and **escalates to a human** the moment a red-flag
symptom appears.

Built for the [AssemblyAI Voice Agent Hackathon](https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon)
(September 2026).

> **This is not a medical device and does not replace clinical care.** Vera is a
> follow-up assistant. It never diagnoses, never prescribes, and escalates to a
> human clinician on any red flag. Every clinical statement it makes is a
> citation from a document, not a generation.

## Provenance

This project builds on **Vera**, my own MIT-licensed project from August 2026
([LuisRG12/vera_voice_agent](https://github.com/LuisRG12/vera_voice_agent)),
where the dialogue state machine, the hybrid retrieval layer and the
deterministic safety engine were written.

Built **during the hackathon window** (September 2026):

- Speech recognition moved from a local Vosk model to **AssemblyAI
  Universal-Streaming** (multilingual), including clinical keyterm prompting.
- Language model moved from a local Llama 3.2 to **Claude via the AssemblyAI LLM
  Gateway**, with structured outputs replacing grammar-constrained decoding.
- Speech synthesis moved from local Piper to **Cartesia**, for a native Colombian
  voice.
- A deployed, publicly reachable demo.
- A knowledge base rebuilt from **freely redistributable clinical guidelines**,
  with citations verified against the retrieved evidence in code.

Nothing in this repository redistributes third-party documents.

## Why the Voice Agent API is not used

AssemblyAI ships a Voice Agent API that bundles speech-to-text, an LLM, text-to-
speech, turn detection and tool calling behind one WebSocket. It is the more
impressive product and it supports Spanish. Vera does not use it, for one
reason.

In that API, tools are **LLM-driven, not forced** — there is no hook that fires
on every user utterance — and session transcripts are **not available while the
session is running**. So a safety check can only run if the language model
decides to invoke it.

For a clinical agent, that inverts the guarantee that matters: the model becomes
the gatekeeper of its own supervision. If a patient says *"I can't breathe"*,
escalation must not depend on a model choosing to call a tool.

Vera therefore drives **Universal-Streaming** directly, so the deterministic
safety engine reads every word the patient says, before and independently of the
language model — and keeps working if the model fails entirely.

See [docs/arquitectura.md](docs/arquitectura.md).

## The knowledge base

Vera answers only from the documents in [`conocimiento/`](conocimiento/), and the
citation is derived by code from what was actually retrieved — not from what the
model claims. Every document declares its source and licence in `fuentes.json`,
and the index refuses anything that does not.

Two layers, because no public corpus holds a given patient's own paperwork:

- **Public-domain clinical guidance in Spanish** — MedlinePlus health topics and
  NIDDK pages, both US federal works, quoted verbatim.
- **One discharge plan, fictional and declared as such**, standing in for what a
  hospital writes for the patient it operated on. It is demonstration material
  and not clinical advice for anyone.

Nothing in this repository redistributes copyrighted third-party documents. See
[conocimiento/README.md](conocimiento/README.md) for what was excluded and why.

## Key decisions, and what testing changed

Every decision below was measured, not assumed. The full reasoning — including
the defects each test uncovered — is in the build log,
[docs/bitacora.md](docs/bitacora.md) (Spanish).

- **The safety engine runs before the model, on every word.** A deterministic
  engine with a Colombian clinical lexicon reads each partial transcript as the
  patient speaks. A red-flag alert fires a median of 0.85 s *before* the turn
  even closes, and it keeps working if the model fails entirely.
- **Two safety layers; the escalation is the higher of the two.** An LLM risk
  judge catches what the rules miss ("a pressure *right here* in my chest"). An
  emergency is never worded by the model: that response is written by code and
  starts playing within milliseconds.
- **A citation is derived by code, not declared by the model.** Structured
  output guarantees the *shape* of a citation, not its truth: with the right
  passage withheld, the model cited the wrong ones 3 times out of 3. Vera
  resolves citations against the passages it was actually shown that turn.
- **A question the documents can't answer never reaches the model.** The
  evidence threshold was calibrated against this corpus (34 questions, some of
  them with the fillers and vocatives of real phone speech): 0 unsupported
  clinical answers. "Can I have a beer?" gets a fixed, code-written abstention.
- **Retrieval bugs were found by using it, not by reading it.** Rank fusion
  treated a zero-information keyword ranking as real; keyword search ranked by
  filler words ("oiga", "me", "puedo"); and a gallbladder patient was answered
  from the appendicitis guide. Each fix is in the log, with its measurement.
- **Barge-in that actually stops the audio.** Cancelling generation wasn't
  enough — the audio already in the browser kept playing. Interrupting Vera now
  drops it, and an interrupted turn still gets its risk assessment.

## Measured

Deterministic harnesses, no network, run in seconds:
`uv run python -m evals.<name>` for `turno` (46 checks), `citas` (18),
`lexico_colombiano` (120), `decision_seguridad` (24), `eco` (27), `limites` (11).
`evals.conocimiento` calibrates the evidence threshold against the real index,
and `scripts/ensayo.py` runs a full conversation against the live model.

## Run it

```bash
uv sync
cp .env.example .env        # add ASSEMBLYAI_API_KEY and CARTESIA_API_KEY
uv run python scripts/indice.py
uv run uvicorn server.main:app --port 7860
```

Or with Docker, which is how it is deployed: `docker build -t vera .` and
`docker run -p 7860:7860 --env-file .env vera`.

## Status

See [docs/etapas.md](docs/etapas.md) for the staged build.
