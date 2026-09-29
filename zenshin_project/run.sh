#!/bin/bash
# Ninja Zenshin Tracker - Script de inicio rapido (Linux / macOS)
# Uso: chmod +x run.sh && ./run.sh

cd "$(dirname "$0")"

echo "=============================================="
echo "  Ninja Zenshin Tracker v23"
echo "  Modo Sabio de los Seis Caminos"
echo "=============================================="
echo ""

# Verificar Python
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo "ERROR: Python no esta instalado. Descargalo desde https://python.org"
    exit 1
fi

echo "Usando: $($PYTHON --version)"
echo "Iniciando tracker en http://localhost:8000 ..."
echo "Presiona Ctrl+C para detener."
echo ""

$PYTHON zenshin_tracker.py "$@"
