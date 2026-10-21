#!/bin/bash

# Kaggle credential comes from the environment only -- never hardcode secrets.
if [ -z "${KAGGLE_API_TOKEN:-}" ]; then
    echo "ERROR: KAGGLE_API_TOKEN is not set or empty." >&2
    echo "       Export it before running, e.g.:" >&2
    echo "         export KAGGLE_API_TOKEN=<your-kaggle-api-token>" >&2
    echo "       (or place credentials in ~/.kaggle/kaggle.json)." >&2
    exit 1
fi
export KAGGLE_API_TOKEN

echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║                    UPLOADING DATASET & NOTEBOOK                           ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Update dataset
echo "📦 Updating dataset..."
cd kaggle_dataset
/home/peter/.local/bin/kaggle datasets version -m "Updated with complete training data" 2>&1 | tail -3
cd ..

echo ""
echo "📓 Creating notebook..."

# Create kernel metadata
mkdir -p kaggle_notebook_dir
cp kaggle_notebook.ipynb kaggle_notebook_dir/

cat > kaggle_notebook_dir/kernel-metadata.json << 'METAEOF'
{
  "id": "zolai-llm-finetuning-t4x2",
  "title": "Zolai LLM Fine-Tuning on T4x2",
  "code_file": "kaggle_notebook.ipynb",
  "language": "python",
  "kernel_type": "notebook",
  "is_private": false,
  "enable_gpu": true,
  "enable_tpu": false,
  "dataset_sources": [
    "peterpausianlian/zolai-llm-training-dataset"
  ],
  "docker_image_pinning": "python310"
}
METAEOF

# Upload notebook
cd kaggle_notebook_dir
/home/peter/.local/bin/kaggle kernels push -p . 2>&1 | tail -5
cd ..

echo ""
echo "════════════════════════════════════════════════════════════════════════════"
echo "✅ Upload complete!"
echo "════════════════════════════════════════════════════════════════════════════"
echo ""
echo "Dataset: https://www.kaggle.com/datasets/peterpausianlian/zolai-llm-training-dataset"
echo "Notebook: https://www.kaggle.com/code/peterpausianlian/zolai-llm-finetuning-t4x2"
echo ""
echo "Next: Go to notebook and run training!"
echo ""
