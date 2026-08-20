# NAM A2 Full baseline factory

This is the streaming 480-model baseline pipeline for TOP Arena:

1. One local BIAS X instance renders the canonical 190-second NAM v3 dry input at
   every one of the 48 amps × 10 positions.
2. Four upload workers send each PCM-24 FLAC capture to S3. A ready-marker JSON is
   published only after its wet audio is durable.
3. The bestia service discovers ready markers continuously. Two trainer threads are
   pinned to physical GPUs 2 and 3 and train official NAM 0.13 A2 Full WaveNets for
   exactly 200 epochs. GPUs 0 and 1 remain untouched.
4. Four CPU inference workers render the existing 50 normalized dry FLACs through
   each trained model while the GPU trainers continue with later positions.
5. The model, exact configs, training log, 50 NAM FLACs, and a metadata document
   linking amp, position, dry, BIAS reference, and NAM output are uploaded to S3.

The producer and bestia worker use SQLite state and deterministic S3 keys. Both are
safe to restart. Audio uploads are always lossless 24-bit FLAC.

Pilot one position:

```bash
PY=/Users/michalwarda/Projects/qlamp/.venv/bin/python
$PY -m tools.nam_baselines produce --amp "Blackface 63" --position-limit 1
```

Launch all remaining training captures, then resume the original 24,000-reference
job after all 480 long captures have been queued:

```bash
$PY -m tools.nam_baselines launch-producer --resume-reference-corpus
```

The S3 namespace is:

```text
s3://qlamp-training-artifacts-088543363904/
  parametric-amplifier/public/top-arena/reference-corpus/v1/nam-a2-full/v1/
```
