#!/usr/bin/env bash
# Аудит відомих вразливостей закріплених залежностей (pip-audit): requirements.txt + constraints.txt
# (прямі й транзитивні). НЕ перевіряє adafruit-* (апаратні, на цій машині їх не встановити) —
# перевіряти на самому Pi.
#   pip install pip-audit          # у dev-середовищі, не на Pi
#   scripts/audit_deps.sh
# Запускати раз на квартал і перед оновленням constraints.txt. Потребує мережі (PyPI).
# Код виходу 1 — знайдено вразливості.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v pip-audit >/dev/null 2>&1; then
  echo "!! pip-audit не встановлено: pip install pip-audit" >&2
  exit 2
fi
COMBINED="$(mktemp)"
trap 'rm -f "$COMBINED"' EXIT
# Лише точні піни name==version (коментарі й порожні рядки відкидаємо)
grep -hE '^[A-Za-z0-9_.-]+==' "$ROOT/requirements.txt" "$ROOT/constraints.txt" | sed 's/[[:space:]]*;.*//' > "$COMBINED"
echo "==> Перевіряю $(wc -l < "$COMBINED") закріплених пакетів"
pip-audit --no-deps --disable-pip -r "$COMBINED"
