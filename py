#!/usr/bin/env bash
# Run anything in the repo venv with its CUDA libs on the path (ctranslate2 needs cuBLAS 12 + cuDNN 9).
here="$(cd "$(dirname "$0")" && pwd)"
sp="$(echo "$here"/.venv/lib/python3*/site-packages)"
export LD_LIBRARY_PATH="$sp/nvidia/cublas/lib:$sp/nvidia/cudnn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$here/.venv/bin/python" "$@"
