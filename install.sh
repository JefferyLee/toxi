#!/usr/bin/env bash
# toxi one-shot installer: libtoxcore -> Python installer -> engine -> identity + service
# -> the adapter(s) you ask for.  Safe to re-run; it upgrades what is already there.
#
#   curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh | bash
#   curl -fsSL ... | bash -s -- --for headless --name castle-op --operator jeff
#
# Targets (--for):  claude   Claude Code plugin + statusLine (default when `claude` is found)
#                   codex    Codex MCP server + plugin
#                   headless a bot/script: identity, card, service, pairing hint (default otherwise)
#                   all      every adapter that is installed on this machine
# Other flags:      --ref <git ref>  install the engine from that git ref instead of PyPI
#                   --name/--operator/--role/--runtime  card fields for `--for headless`
set -euo pipefail

REPO="JefferyLee/toxi"; REPO_URL="https://github.com/${REPO}"
PLUGINS_DIR="${HOME}/.claude/plugins"; MARKETPLACE_DIR="${PLUGINS_DIR}/marketplaces/toxi"
CACHE_ROOT="${PLUGINS_DIR}/cache/toxi/toxi"
FOR=""; REF=""; NAME=""; OPERATOR=""; ROLE=""; RUNTIME="other"
while [ $# -gt 0 ]; do
  case "$1" in
    --for) FOR="$2"; shift 2;;  --ref) REF="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;  --operator) OPERATOR="$2"; shift 2;;
    --role) ROLE="$2"; shift 2;;  --runtime) RUNTIME="$2"; shift 2;;
    -h|--help) sed -n '2,15p' "$0"; exit 0;;
    *) echo "unknown flag: $1" >&2; exit 2;;
  esac
done

info() { printf '\033[1;34m→\033[0m %s\n' "$1"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m⚠\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$1" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
OS="$(uname -s)"
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:${PATH}"

if [ -z "$FOR" ]; then
  if have claude || [ -d "${HOME}/.claude" ]; then FOR=claude; else FOR=headless; fi
fi

# 1. libtoxcore -----------------------------------------------------------------
if pkg-config --exists toxcore 2>/dev/null \
   || ls /opt/homebrew/lib/libtoxcore* /usr/local/lib/libtoxcore* /usr/lib/libtoxcore* \
         /usr/lib/*/libtoxcore* >/dev/null 2>&1; then
  ok "libtoxcore present"
elif [ "$OS" = "Darwin" ] && have brew; then
  info "Installing libtoxcore (brew install toxcore)…"; brew install toxcore
elif have apt-get; then
  info "Installing libtoxcore (apt)…"; sudo apt-get install -y libtoxcore2 || sudo apt-get install -y libtoxcore-dev
elif have dnf; then
  info "Installing libtoxcore (dnf)…"; sudo dnf install -y toxcore
elif have pacman; then
  info "Installing libtoxcore (pacman)…"; sudo pacman -S --noconfirm toxcore
else
  die "libtoxcore not found and no known package manager. Install it (macOS: brew install toxcore; Debian: libtoxcore2) and re-run."
fi

# 2. a Python app installer: pipx, else uv, else pip --user --------------------
PY_MIN_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3,10)))' 2>/dev/null || echo 0)
[ "$PY_MIN_OK" = 1 ] || warn "python3 is older than 3.10; pipx/uv will pick a newer one if available"
if have pipx; then INSTALLER=pipx
elif have uv; then INSTALLER=uv
elif [ "$OS" = "Darwin" ] && have brew; then info "Installing pipx…"; brew install pipx; pipx ensurepath >/dev/null 2>&1 || true; INSTALLER=pipx
elif python3 -m pip --version >/dev/null 2>&1; then
  info "Installing pipx via pip…"; python3 -m pip install --user pipx >/dev/null 2>&1 && INSTALLER=pipx || INSTALLER=pip
else
  INSTALLER=pip
fi
ok "installer: ${INSTALLER}"

# 3. engine (install or upgrade) -------------------------------------------------
# From PyPI when the package is published there; otherwise (or with --ref) from
# GitHub — the latest release tag by default, so users never depend on PyPI.
if [ -z "$REF" ] && ! curl -fsS -o /dev/null "https://pypi.org/pypi/toxi/json" 2>/dev/null; then
  REF="$(git ls-remote --tags --refs "${REPO_URL}" 'v*' 2>/dev/null | sed 's|.*/||' | sort -V | tail -1)"
  [ -n "$REF" ] || REF=master
  info "toxi is not on PyPI yet — installing release ${REF} from GitHub"
fi
if [ -n "$REF" ]; then SPEC="toxi[mcp] @ git+${REPO_URL}@${REF}"; else SPEC="toxi[mcp]"; fi
info "Installing/upgrading toxi engine (${SPEC})…"
engine_install() {
  case "$INSTALLER" in
    pipx)
      if pipx list 2>/dev/null | grep -q 'package toxi'; then
        pipx install --force "$1" >/dev/null 2>&1 || pipx install --force --backend pip "$1" >/dev/null
      else
        pipx install "$1" >/dev/null 2>&1 || pipx install --backend pip "$1" >/dev/null
      fi
      pipx inject toxi 'mcp>=1.2,<2' >/dev/null 2>&1 || true;;
    uv)  uv tool install --force "$1" >/dev/null;;
    pip) python3 -m pip install --user --upgrade "$1" >/dev/null || python3 -m pip install --user --upgrade --break-system-packages "$1" >/dev/null;;
  esac
}
engine_install "$SPEC"
have toxi || die "toxi is not on PATH after install — open a new shell (or add ~/.local/bin to PATH) and re-run."
ok "engine: $(toxi --version 2>/dev/null)"

# 4. identity + daemon + keep-alive ----------------------------------------------------
info "Identity + daemon…"
toxi setup engine
if toxi service install >/dev/null 2>&1; then ok "service installed (daemon survives reboots)"
else warn "no service manager here — keep the daemon alive from cron:  */10 * * * * $(command -v toxi) daemon start >/dev/null 2>&1"; fi

# 5. adapters ------------------------------------------------------------------------------
want() { [ "$FOR" = "$1" ] || [ "$FOR" = all ]; }

if want claude; then
  if ! have claude && [ ! -d "${HOME}/.claude" ]; then
    warn "Claude Code not detected — skipping the plugin (re-run with --for claude after installing it)."
  else
    info "Claude Code: plugin + statusLine…"
    if [ -d "${MARKETPLACE_DIR}/.git" ]; then
      git -C "${MARKETPLACE_DIR}" fetch --quiet origin && git -C "${MARKETPLACE_DIR}" reset --quiet --hard "origin/${REF:-HEAD}" 2>/dev/null \
        || git -C "${MARKETPLACE_DIR}" reset --quiet --hard origin/HEAD
    else
      rm -rf "${MARKETPLACE_DIR}"; mkdir -p "$(dirname "${MARKETPLACE_DIR}")"
      git clone --quiet --depth 1 ${REF:+--branch "$REF"} "${REPO_URL}" "${MARKETPLACE_DIR}"
    fi
    PLUGIN_SRC="${MARKETPLACE_DIR}/claude-code-plugin"
    VERSION="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "${PLUGIN_SRC}/.claude-plugin/plugin.json")"
    SHA="$(git -C "${MARKETPLACE_DIR}" rev-parse HEAD)"
    DEST="${CACHE_ROOT}/${VERSION}"; rm -rf "${DEST}"; mkdir -p "${DEST}"; cp -R "${PLUGIN_SRC}/." "${DEST}/"
    # Older version dirs stay: running sessions call their hooks from them until /reload-plugins.
    python3 - "${VERSION}" "${SHA}" "${DEST}" "${MARKETPLACE_DIR}" "${REPO}" <<'PY'
import json, os, sys, time
version, sha, install_path, marketplace, repo = sys.argv[1:6]
home = os.path.expanduser("~"); plugins = os.path.join(home, ".claude", "plugins")
now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
def load(p):
    try:
        with open(p) as f: return json.load(f)
    except (FileNotFoundError, ValueError): return {}
def dump(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f: json.dump(d, f, indent=2)
ip = os.path.join(plugins, "installed_plugins.json")
inst = load(ip) or {"version": 2, "plugins": {}}; inst.setdefault("plugins", {})
entries = inst["plugins"].get("toxi@toxi") or []
entry = entries[0] if entries else {"scope": "user", "installedAt": now}
entry.update({"version": version, "gitCommitSha": sha, "installPath": install_path, "lastUpdated": now})
inst["plugins"]["toxi@toxi"] = [entry]; dump(ip, inst)
km = os.path.join(plugins, "known_marketplaces.json"); known = load(km)
known.setdefault("toxi", {"source": {"source": "github", "repo": repo}, "installLocation": marketplace, "lastUpdated": now}); dump(km, known)
sp = os.path.join(home, ".claude", "settings.json"); st = load(sp)
st.setdefault("enabledPlugins", {})["toxi@toxi"] = True; dump(sp, st)
PY
    toxi setup claude >/dev/null 2>&1 || true
    ok "Claude Code plugin ${VERSION} (${SHA:0:7}); statusLine wired"
    if [ -n "${CLAUDECODE:-}" ]; then warn "inside Claude Code: run /reload-plugins"; else ok "restart Claude Code sessions to load the hooks"; fi
  fi
fi

if want codex; then
  if have codex; then info "Codex: MCP server + plugin…"; toxi setup codex || warn "Codex wiring reported problems (see above)"
  else warn "codex CLI not found — skipping (re-run with --for codex after installing it)"; fi
fi

if want headless; then
  info "Headless identity card…"
  if [ -t 0 ]; then
    [ -n "$NAME" ] || { printf 'Name for this identity (shown to peers) [%s]: ' "$(hostname -s)"; read -r NAME || true; }
    [ -n "$OPERATOR" ] || { printf 'Who operates it (e.g. your name): '; read -r OPERATOR || true; }
  fi
  ARGS=(--kind agent --runtime "$RUNTIME" --no-service)
  [ -n "$NAME" ] && ARGS+=(--name "$NAME"); [ -n "$OPERATOR" ] && ARGS+=(--operator "$OPERATOR"); [ -n "$ROLE" ] && ARGS+=(--role "$ROLE")
  toxi setup headless "${ARGS[@]}"
fi

# 6. check --------------------------------------------------------------------------------
echo; info "toxi doctor"; toxi doctor || true
ok "Done."
