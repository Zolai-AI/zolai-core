#!/usr/bin/env python3
"""Smart Zolai installer — detects system and installs only what's needed."""
import subprocess, sys, os, shutil, platform, json
from pathlib import Path

GREEN = "\033[0;32m"
YELL = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"

def run(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def detect_gpu():
    """Check for NVIDIA GPU and CUDA driver."""
    nvidia_smi, _, rc = run("nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null || echo 'NONE'")
    if rc != 0 or "NONE" in nvidia_smi or not nvidia_smi.strip():
        return None
    return nvidia_smi.strip().split("\n")

def detect_ram():
    """Get available RAM in GB."""
    out, _, _ = run("free -g | awk '/^Mem:/ {print $2}'")
    return int(out) if out.isdigit() else 4

def detect_disk():
    """Get free disk space in GB."""
    out, _, _ = run("df -BG / | awk 'NR==2 {print $4}' | tr -d 'G'")
    return int(out) if out.isdigit() else 1

def pip_list():
    """Get installed packages."""
    out, _, _ = run(f"{sys.executable} -m pip list --format=json")
    try:
        return {p["name"].lower(): p["version"] for p in json.loads(out)}
    except:
        return {}

def main():
    print(f"\n{GREEN}╔══════════════════════════════════════════╗{NC}")
    print(f"{GREEN}║{NC}  {YELL}🦜 Zolai Smart Installer{NC}               {GREEN}║{NC}")
    print(f"{GREEN}╚══════════════════════════════════════════╝{NC}\n")

    # System info
    gpu = detect_gpu()
    ram = detect_ram()
    disk = detect_disk()
    installed = pip_list()
    python_ver = platform.python_version()
    system = platform.system()
    arch = platform.machine()

    print(f"  {YELL}System:{NC} {system} {arch} | Python {python_ver}")
    print(f"  {YELL}RAM:{NC} {ram}GB | {YELL}Disk free:{NC} {disk}GB")
    if gpu:
        print(f"  {YELL}GPU:{NC} {GREEN}NVIDIA detected ({len(gpu)} card(s)){NC}")
        for g in gpu:
            print(f"    → {g.strip()}")
    else:
        print(f"  {YELL}GPU:{NC} {RED}None detected (CPU-only){NC}")
    print()

    # Check space
    if disk < 2:
        print(f"  {RED}⚠️  WARNING: Only {disk}GB free disk space!{NC}")
        print(f"  {RED}   Install may fail. Clear space first.{NC}\n")

    # Determine what to install
    install_packages = []
    install_groups = []

    # Core packages (always needed)
    core = [
        ("fastapi>=0.134", "API server"),
        ("uvicorn>=0.27", "ASGI server"),
        ("typer>=0.12", "CLI framework"),
        ("rich>=13.7", "Rich terminal output"),
        ("python-dotenv>=1.0", "Environment variables"),
        ("httpx>=0.28", "HTTP client"),
        ("aiofiles>=23.2", "Async file I/O"),
        ("pyyaml>=6.0", "YAML parser"),
        ("pydantic>=2.12", "Data validation"),
        ("sqlalchemy>=2.0", "Database ORM"),
        ("psycopg2-binary>=2.9", "PostgreSQL driver"),
        ("pandas>=2.0", "Data processing"),
        ("numpy>=2.4", "Numerical computing"),
        ("scikit-learn>=1.8", "ML utilities"),
        ("transformers>=5.0", "HuggingFace transformers"),
        ("datasets>=4.8", "HuggingFace datasets"),
        ("sentence-transformers>=5.0", "Sentence embeddings"),
        ("matplotlib>=3.5", "Plotting"),
        ("tqdm>=4.66", "Progress bars"),
        ("kaggle>=1.6.0", "Kaggle CLI"),
        ("kagglehub>=0.3", "KaggleHub"),
        ("huggingface_hub>=0.36", "HF Hub"),
        ("mistralai>=1.0", "Mistral AI"),
        ("duckduckgo-search>=5.0", "Web search"),
        ("beautifulsoup4>=4.12", "HTML parser"),
        ("lxml>=5.1", "XML parser"),
        ("python-dotenv>=1.0", "Env files"),
    ]

    # Dev packages (always needed)
    dev = [
        ("pytest>=8.0", "Testing"),
        ("ruff>=0.3", "Linting"),
        ("mypy>=1.8", "Type checking"),
    ]

    # GPU packages (only if NVIDIA GPU detected)
    gpu_packages = [
        ("torch>=2.10", "Deep learning (GPU)"),
        ("bitsandbytes>=0.43.0", "Quantization"),
        ("accelerate>=1.13", "Training acceleration"),
        ("peft>=0.19", "LoRA fine-tuning"),
        ("trl>=1.1", "RL training"),
        ("scipy>=1.10", "Scientific computing"),
    ]

    # CPU packages (always needed as torch replacement)
    cpu_packages = [
        ("torch>=2.10", "Deep learning (CPU)"),
    ]

    print(f"  {YELL}Installing packages...{NC}\n")

    # Install core packages
    print(f"  {GREEN}[1/3] Core packages...{NC}")
    pkg_list = " ".join([p[0] for p in core if p[0].split('>=')[0].lower() not in installed])
    if pkg_list:
        r = run(f'{sys.executable} -m pip install -q {" ".join(core)}', check=False)
        if r[2] == 0:
            print(f"    {GREEN}✅ Core packages installed{NC}")
        else:
            print(f"    {RED}⚠️  Core install had issues{NC}")
    else:
        print(f"    {GREEN}✅ All core packages already installed{NC}")

    # Install dev packages
    print(f"  {GREEN}[2/3] Dev packages...{NC}")
    pkg_list = " ".join([p[0] for p in dev if p[0].split('>=')[0].lower() not in installed])
    if pkg_list:
        r = run(f'{sys.executable} -m pip install -q {" ".join(dev)}', check=False)
        if r[2] == 0:
            print(f"    {GREEN}✅ Dev packages installed{NC}")
        else:
            print(f"    {RED}⚠️  Dev install had issues{NC}")
    else:
        print(f"    {GREEN}✅ All dev packages already installed{NC}")

    # Install GPU or CPU packages
    print(f"  {GREEN}[3/3] Training packages...{NC}")
    if gpu:
        print(f"    {YELL}NVIDIA GPU detected → installing GPU packages...{NC}")
        # Check disk space for GPU packages (~5GB)
        if disk < 5:
            print(f"    {RED}⚠️  Not enough disk space for GPU packages (need ~5GB, have {disk}GB){NC}")
            print(f"    {YELL}Installing CPU-only torch instead...{NC}")
            r = run(f'{sys.executable} -m pip install -q --index-url https://download.pytorch.org/whl/cpu torch', check=False)
        else:
            r = run(f'{sys.executable} -m pip install -q torch>=2.10 bitsandbytes>=0.43.0 accelerate>=1.13 peft>=0.19 trl>=1.1 scipy>=1.10', check=False)
            if r[2] == 0:
                print(f"    {GREEN}✅ GPU packages installed{NC}")
            else:
                print(f"    {RED}⚠️  GPU install failed, trying CPU version...{NC}")
                r = run(f'{sys.executable} -m pip install -q --index-url https://download.pytorch.org/whl/cpu torch', check=False)
    else:
        print(f"    {YELL}CPU-only system → installing CPU-only packages...{NC}")
        r = run(f'{sys.executable} -m pip install -q --index-url https://download.pytorch.org/whl/cpu torch', check=False)
        if r[2] == 0:
            print(f"    {GREEN}✅ CPU-only torch installed (saves ~3GB){NC}")
        else:
            # Try pip default torch
            r = run(f'{sys.executable} -m pip install -q torch>=2.10', check=False)
            if r[2] == 0:
                print(f"    {GREEN}✅ Torch installed{NC}")
            else:
                print(f"    {RED}⚠️  Torch install failed (check network){NC}")

    # Install zolai itself
    print(f"\n  {GREEN}[Final] Installing zolai package...{NC}")
    r = run(f'{sys.executable} -m pip install -q -e .', check=False, cwd=str(Path(__file__).parent.parent))
    if r[2] == 0:
        print(f"    {GREEN}✅ zolai installed{NC}")
    else:
        print(f"    {RED}⚠️  zolai install had issues{NC}")

    # Summary
    print(f"\n{GREEN}╔══════════════════════════════════════════╗{NC}")
    print(f"{GREEN}║${NC}  {YELL}Installation Complete{NC}                    {GREEN}║{NC}")
    print(f"{GREEN}╚══════════════════════════════════════════╝{NC}")
    print(f"\n  {YELL}Quick start:{NC}")
    print(f"    cd zolai-core && ./menu.sh")
    print(f"    Or: zolai serve")
    print(f"\n  {YELL}For GPU training later:{NC}")
    print(f"    pip install -e '.[gpu]'")
    print(f"\n  {YELL}For CPU-only (what you have):{NC}")
    print(f"    pip install -e .  (already done)")

if __name__ == "__main__":
    main()
