# Disable the debugsource / debuginfo subpackages. Our package bundles a
# pre-built Python venv; there are no source files for RPM to extract debug
# symbols from, and Fedora's default policy would otherwise fail with
# "Empty files file ... debugsourcefiles.list".
%global debug_package %{nil}

# Disable the brp-check-rpaths step. The bundled python-build-standalone
# interpreter ships tcl/tk libraries (libtcl9.0.so, libtcl9tk9.0.so) with
# RPATHs baked in from PBS's own build environment (/tools/deps/lib). Those
# paths don't exist on the target host, but they are harmless: tkinter is
# not used by this app, and the bundled libs find their actual deps via
# $ORIGIN at runtime. check-rpaths is too strict to allow a sealed bundle.
%global __brp_check_rpaths %{nil}

# Disable the brp-mangle-shebangs step. The PBS stdlib ships some files
# (e.g. encodings/rot_13.py) with `#!/usr/bin/env python` (no `3`), which
# Fedora's strict shebang policy treats as ambiguous and aborts on. The
# stdlib scripts are never invoked as executables in our deployment — the
# bundled python interpreter loads them as modules — so the shebang text
# is purely cosmetic. Disable the policy for the sealed bundle.
%global __brp_mangle_shebangs %{nil}

# python-build-standalone ships its .so files without GNU build-id sections
# (they are prebuilt; debug-link / build-id is not in PBS's pipeline). The
# default Fedora policy turns this into a fatal "Missing build-id" error.
# Demote it to a warning so the sealed bundle ships as-is.
%global _missing_build_ids_terminate_build 0

Name:           eth-validator-stats
Version:        0.4.0
Release:        1%{?dist}
Summary:        Ethereum validator stats watcher

License:        MIT
URL:            https://github.com/Workharu/eth-validator-stats
Source0:        %{name}-%{version}.tar.gz

# Not noarch: the bundled venv contains arch-specific binaries (PyYAML's
# libyaml C extension _yaml.so plus symlinks to the system python).
BuildRequires:  systemd-rpm-macros
BuildRequires:  tar
BuildRequires:  curl
# python3.11 is no longer needed at build time — uv downloads a self-contained
# python-build-standalone interpreter via `uv python install`.

Requires(pre):  shadow-utils
Requires(post): systemd
Requires(preun): systemd
Requires(postun): systemd

%description
Tiny self-hosted CLI for Ethereum validator stats, backed by your own
beacon node. Provides one-shot, cron-style, and long-running supervised
modes. Talks to any beacon node implementing the standard Ethereum
Beacon API (Prysm, Lighthouse, Teku, Nimbus, Lodestar).

This package installs a system service that runs the watcher under
systemd. Run `sudo eth-validator-stats init --system` after install
to configure.

%prep
%autosetup

%build
# nothing to compile

%install
# Only stage dirs the package will own. The /etc and /var/lib directories
# are created in the post-install scriptlet so RPM doesn't own them — that
# way user-created files inside them survive `rpm -e` cleanly.
mkdir -p %{buildroot}/opt/%{name} \
         %{buildroot}%{_bindir} \
         %{buildroot}%{_unitdir}

# Bundle a self-contained python-build-standalone interpreter via uv.
# This drops the runtime dependency on system python3.11.
uv python install --install-dir %{buildroot}/opt/%{name}/python 3.11

# `uv python install` creates a stable-name symlink alongside the versioned
# interpreter dir, e.g.
#   cpython-3.11-linux-x86_64-gnu -> <buildroot>/.../cpython-3.11.15-linux-x86_64-gnu
# The target is an absolute path through BuildRoot, which RPM rejects with
# "Symlink points to BuildRoot". Rewrite each such symlink to be a relative
# sibling reference so it resolves correctly after install.
for stable in %{buildroot}/opt/%{name}/python/cpython-*-linux-*-gnu; do
    [ -L "$stable" ] || continue
    target=$(basename "$(readlink "$stable")")
    rm -f "$stable"
    ln -s "$target" "$stable"
done

# Locate the installed interpreter and use it to build the venv.
PBS_PYTHON=$(find %{buildroot}/opt/%{name}/python -type f -name python3.11 | head -n1)
test -n "$PBS_PYTHON" || { echo "ERROR: no python3.11 found after uv install"; exit 1; }
$PBS_PYTHON -m venv --copies %{buildroot}/opt/%{name}/venv
%{buildroot}/opt/%{name}/venv/bin/pip install --no-cache-dir .

# Drop activation scripts: not needed at runtime (the service ExecStart calls
# the venv binary directly) and they embed the buildroot path in VIRTUAL_ENV=...
rm -f %{buildroot}/opt/%{name}/venv/bin/activate \
      %{buildroot}/opt/%{name}/venv/bin/activate.csh \
      %{buildroot}/opt/%{name}/venv/bin/activate.fish \
      %{buildroot}/opt/%{name}/venv/bin/activate.nu \
      %{buildroot}/opt/%{name}/venv/bin/activate.ps1

# Strip __pycache__ / .pyc first across the whole bundled tree — they
# binary-encode the buildroot path and would survive the text-file rewrite.
find %{buildroot}/opt/%{name} -type d -name '__pycache__' -prune -exec rm -rf {} +
find %{buildroot}/opt/%{name} -name '*.pyc' -delete

# Rewrite the buildroot path out of every TEXT file in the bundled tree.
# Walk the whole /opt/%{name} (both venv AND the python-build-standalone
# base interpreter) — _sysconfigdata*.py in the PBS tree otherwise carries
# the buildroot path and trips %check-buildroot. grep -I excludes binaries
# (e.g. PyYAML's _yaml.so) so this is safe to run broadly.
grep -rlI "%{buildroot}" %{buildroot}/opt/%{name} 2>/dev/null \
    | xargs -r sed -i "s|%{buildroot}||g"

# Symlink /usr/bin entry points — both the canonical long name and the
# short `evs` alias declared in pyproject.toml's [project.scripts].
# Without the second symlink, `evs status` returns "command not found"
# even though the binary exists at .../venv/bin/evs.
ln -sf /opt/%{name}/venv/bin/%{name} %{buildroot}%{_bindir}/%{name}
ln -sf /opt/%{name}/venv/bin/evs %{buildroot}%{_bindir}/evs

# Write the systemd unit.
cat > %{buildroot}%{_unitdir}/%{name}.service <<'EOF'
[Unit]
Description=eth-validator-stats watcher
Documentation=https://github.com/Workharu/eth-validator-stats
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/eth-validator-stats/config.yml

[Service]
Type=simple
User=eth-validator-stats
Group=eth-validator-stats
ExecStart=/opt/eth-validator-stats/venv/bin/eth-validator-stats watch
Environment="ETH_VALIDATOR_STATS_CONFIG=/etc/eth-validator-stats/config.yml"
Environment="ETH_VALIDATOR_STATS_STATE=/var/lib/eth-validator-stats/state.json"
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

%pre
getent group eth-validator-stats >/dev/null || groupadd -r eth-validator-stats
getent passwd eth-validator-stats >/dev/null || \
    useradd -r -g eth-validator-stats \
            -d /var/lib/eth-validator-stats \
            -s /sbin/nologin \
            -c "eth-validator-stats service user" \
            eth-validator-stats
exit 0

%post
# Create config + state dirs here (not in the install section) so RPM doesn't
# own them, which means user-created files inside survive `rpm -e`.
mkdir -p /etc/%{name} /var/lib/%{name}
chown -R eth-validator-stats:eth-validator-stats /etc/%{name} /var/lib/%{name}
# /etc dir: 0755 so non-group users can at least see the file exists
# (standard for /etc). The config file itself is created with 0640 by
# `init --system`, so secrets stay group-read-only.
# /var/lib dir: 0750, only the service user touches state.
chmod 0755 /etc/%{name}
chmod 0750 /var/lib/%{name}

# Upgrade-time: if /etc/<pkg>/config.yml was previously chmod 0640
# (the default in <= 0.3.6), bump it to 0644 so non-group users
# can read the config without sudo. Only touch the file if it has
# the exact old default — leave any user-customized perms alone.
if [ -f /etc/%{name}/config.yml ]; then
    cur=$(stat -c '%a' /etc/%{name}/config.yml 2>/dev/null || echo "")
    if [ "$cur" = "640" ]; then
        chmod 0644 /etc/%{name}/config.yml
    fi
fi

%systemd_post %{name}.service

if [ $1 -eq 1 ] ; then
    cat <<'EOF'

eth-validator-stats installed.

Next step:
  sudo eth-validator-stats init --system

`init --system` writes /etc/eth-validator-stats/config.yml AND starts
the service in one command. The systemd unit is already enabled, so on
every subsequent boot it starts automatically.

EOF
fi

%preun
%systemd_preun %{name}.service

%postun
%systemd_postun_with_restart %{name}.service
# On full uninstall ($1 == 0), clean up the bundled tree. The Python
# interpreter creates __pycache__/*.pyc at runtime that rpm doesn't own,
# leaving non-empty dirs behind otherwise. $1 != 0 is an upgrade, so we
# leave files in place — the new version's install phase will overwrite.
# (Reminder for future edits: never write percent-prefixed section
# keywords (install / post / postun / files / prep / build / changelog)
# anywhere in this spec outside their actual section headers. rpmbuild
# matches the keyword by name regardless of position, even inside a
# shell comment, and aborts with "second <section>".)
if [ $1 -eq 0 ]; then
    rm -rf /opt/%{name}
fi

%files
%license LICENSE
%doc README.md
%dir /opt/%{name}
/opt/%{name}/python
/opt/%{name}/venv
%{_bindir}/%{name}
%{_bindir}/evs
%{_unitdir}/%{name}.service
# The config and state directories are intentionally NOT listed here:
# the post-install scriptlet creates them so the package does not own the
# dirs (preserving any user-created contents on uninstall).

%changelog
* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.4.0-1
- Breaking: legacy TOML config support removed. The CLI now reads
  YAML only (.yml / .yaml). The --migrate flag is gone. Every
  release since 0.2.0 has shipped YAML as the canonical format,
  so this should affect no real-world installs.
- Reliability: save_state no longer races between an interactive
  `status` and the `watch` loop. Both writers now use unique tmp
  files instead of a fixed .json.tmp path.
- UX: `init` no longer prints an empty "[]" bracket on prompts
  whose default is the empty string.
- Project hygiene: adopt ruff (lint CI job), add pytest-cov, expand
  the CI test matrix to Python 3.11/3.12/3.13, add the 3.13 trove
  classifier. Pin least-privilege permissions on the pre-release
  workflow's GITHUB_TOKEN.
- Docs: add CHANGELOG.md, SECURITY.md, CONTRIBUTING.md, issue+PR
  templates, and a dependabot.yml that groups runtime vs dev deps.
- Installer: scripts/install.sh documents the trust model and uses
  jq when present, falling back to grep on minimal hosts.
- Fix: spurious MISSED_ATTESTATIONS alerts on freshly-deposited
  validators. pending_initialized and pending_queued are now
  hard-no-alert; active_ongoing is the only state subject to
  missed-attestation checks; active_exiting / active_slashed
  surface as OFFLINE so slow exits aren't silent.
- New: validator lifecycle notifications. Each stage transition on
  the Beacon API status enum fires its own ntfy push, deduplicated
  per-validator per-transition: ACTIVATED, EXIT INITIATED, SLASHED
  (ntfy Priority: urgent), EXITED, WITHDRAWAL READY. All five
  available via `simulate <event>`: activated, exit-initiated,
  slashed, exited, withdrawal-ready.

* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.3.12-1
- New: `eth-validator-stats validators add|list|rm` subcommand group.
  Adds, lists, and removes validators from config.yml without
  editing YAML. `add` verifies the validator on the beacon node;
  `list --status` shows live status + balance; `rm` accepts index,
  pubkey, or label. All three preserve the config file's mode and
  ownership and restart the systemd service afterwards.
- UX: `init` parting message now points at `validators add` instead
  of telling users to hand-edit config.yml. Surfaces the `evs`
  short alias on the same screen.
- Fix: `init` was silently accepting an empty bearer token when the
  user answered "yes" to "Does this node need auth?" Now re-prompts.
- Internal: code-review pass fixed three pubkey-only edge cases in
  the new validators module (`list --status`, `add --no-verify`
  echo, `rm` confirmation text).

* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.3.11-1
- New: one-liner installer at scripts/install.sh that handles .deb,
  .rpm, and pipx fallback from a single curl-pipe-to-bash command.
- Fix: `evs status` returned "command not found" after a .rpm
  install. The spec now symlinks both names into /usr/bin and the
  files manifest lists both.
- Fix: arrow keys work during `init` prompts (added `import
  readline` to the CLI entry path).
- Fix: ntfy verification push sent by `init` now carries the
  project icon, matching every real alert that follows.
- UX: `init` prints a tip about the `evs` alias on success.

* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.3.10-1
- Fix: `sudo eth-validator-stats init` (without --system) on an
  .rpm-installed host previously wrote the config to
  /root/.config/eth-validator-stats/config.yml — because HOME=/root
  under sudo. The systemd unit looks at /etc/.../config.yml, so the
  service got permanently stuck "inactive (dead) - start condition
  unmet". Init now auto-promotes to --system when EUID==0 and the
  eth-validator-stats system user exists. The escape hatch for the
  rare "root but per-user path" case is to set
  ETH_VALIDATOR_STATS_CONFIG explicitly.

* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.3.9-1
- Fix: this spec now compiles — bare section keywords (install /
  post / postun / files) inside shell comments and changelog text
  were tripping rpmbuild's parser ("second install" at line 205 of
  the 0.3.8 spec). Rephrased the offending occurrences to drop the
  percent prefix. The .deb side was unaffected and shipped
  correctly in 0.3.8.

* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.3.8-1
- New: every ntfy push now carries a branded app icon. The default
  URL points at assets/notification-icon.png in this repo, served
  via raw.githubusercontent.com. ntfy clients (Android, iOS, web)
  fetch and cache the image, so it's instantly recognizable as a
  push from eth-validator-stats. Configurable via `alerts.icon_url`
  — set to "" to disable, or to your own URL to brand pushes
  differently.

* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.3.7-1
- Fix: `init --system` now writes /etc/<pkg>/config.yml with mode
  0644 instead of 0640. Any user can run read-only commands against
  a system install without sudo or group membership. If you do put
  a `beacon_auth_token` for a hosted provider in the file, tighten
  to 0640 manually.
- Fix: post-install scriptlet auto-upgrades existing config files that are still at
  the old 0640 default to 0644 on `dnf install`. Files at any other
  mode are left alone.
- Fix: post-uninstall scriptlet on full uninstall ($1 == 0) now `rm -rf /opt/<pkg>`
  so leftover __pycache__/*.pyc files created by the bundled Python
  interpreter at runtime don't keep the venv dirs around.

* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.3.6-1
- Fix: `eth-validator-stats status` (and the other read-only
  subcommands) no longer crash with a PermissionError traceback when
  invoked as a non-root user against an .rpm-installed system config.
  Python 3.11+ propagates PermissionError from Path.exists() (rather
  than silently returning False as 3.10 did), so the resolver's
  /etc/<pkg>/config.yml existence check was bailing the whole CLI.
  The CLI now produces a clear actionable message instead:
      config at /etc/.../config.yml is not readable by the current
      user. Re-run with sudo, or add yourself to the
      eth-validator-stats group: sudo usermod -aG eth-validator-stats $USER
- Packaging: chmod /etc/eth-validator-stats is now 0755 (was 0750)
  so non-group users can at least see the config file exists. The
  config file itself stays 0640 — secrets (beacon_auth_token,
  ntfy_topic) remain group-readable only.

* Mon May 25 2026 privatejava <privatejava@yahoo.com> - 0.3.5-1
- New: `eth-validator-stats --version` (and `evs --version`) prints
  the installed package version. Reads from importlib.metadata so it
  reflects the actual installed dist regardless of install method.
- New: `eth-validator-stats init --system` now starts the systemd
  service automatically after writing /etc/<pkg>/config.yml. First-
  time setup after `dnf install` is now a single command instead of
  two. Helper is best-effort and non-fatal: silently skips when
  systemctl is unavailable, when the eth-validator-stats.service
  unit isn't installed (e.g. pipx-only), or when start fails (in
  which case it prints a clear pointer at `systemctl start`).
- Internal: uses `systemctl restart` (not `start`) so re-running
  `init --system --force` after a config edit picks up the new
  config without a separate restart step.
- Docs: post-install scriptlet message simplified to a single
  "Next step: sudo eth-validator-stats init --system". Same for the
  .deb postinst message and the four README install sections.

* Sun May 24 2026 privatejava <privatejava@yahoo.com> - 0.3.4-1
- Fix: status / check / info / simulate now find /etc/<pkg>/config.yml
  written by `init --system` or the .rpm post-install. Previously they
  only checked ~/.config and erred out on system installs unless the
  systemd service was used. Resolver order is now:
  $ETH_VALIDATOR_STATS_CONFIG -> /etc -> ~/.config.
- Fix (.deb only): postinst no longer prints the alarming "home dir
  /var/lib/eth-validator-stats can't be accessed: No such file or
  directory" warning during first install. Reordered the steps so
  config and state directories are created before adduser references
  the service user's home. postrm also now removes the system group
  on purge so `apt purge && apt install` is a true first-install test.
- New: short CLI alias `evs` (3 chars). Same binary as
  eth-validator-stats, just easier to type.
- Docs: substantial README polish for onboarding (pick-your-install
  table, example status output, per-install cron path table,
  simulate-subcommand visibility, accurate "what it does NOT do" list,
  removed stale legacy TOML migration section).

* Sun May 24 2026 privatejava <privatejava@yahoo.com> - 0.3.2-1
- Internal: scripts/bump.sh helper to sync version across pyproject.toml,
  the rpm spec, debian/changelog, README install snippets, and uv.lock,
  and to prepend stub changelog entries for the new release.
- Internal: CI now runs `bump.sh --verify` on every push (via a dedicated
  version-consistency job) and at release time across all release jobs,
  so version drift across pinned files is caught before a tag is cut.
- Docs: README install-snippet filenames updated (carried stale 0.3.0
  values at the 0.3.1 release; corrected in a follow-up commit).

* Sun May 24 2026 Workharu <Workharu@users.noreply.github.com> - 0.3.1-1
- New `eth-validator-stats simulate <event>` subcommand fires a single ntfy
  push using the exact title/body template real alerts produce. Useful to
  verify the notification wire without waiting for a real outage. Supports
  missed-attestation, offline, withdrawal, proposing-soon, proposed,
  missed-proposal, blind, recovered.
- Packaging hardening so the .rpm builds cleanly on Fedora 40+: disable
  brp-check-rpaths and brp-mangle-shebangs (PBS bundle is sealed), rewrite
  buildroot paths in the entire /opt/<pkg> tree (was only the venv before),
  rewrite uv's stable-name symlink to be relative, and demote missing
  build-id errors to warnings (PBS .so files don't carry build-ids).

* Sun May 24 2026 Workharu <Workharu@users.noreply.github.com> - 0.3.0-1
- Self-contained .rpm: bundles python-build-standalone via uv, drops the
  runtime dependency on system python3.11.
- Adds x86_64 + aarch64 packages from a CI matrix.
- New eth-validator-stats install-service / uninstall-service subcommands.

* Sun May 24 2026 Workharu <Workharu@users.noreply.github.com> - 0.2.1-1
- Docs-only: correct install instructions in README for Ubuntu 24.04
  (deadsnakes PPA prerequisite) and Fedora; recommend apt install ./path.deb
  over dpkg -i so dependencies auto-resolve.

* Sun May 24 2026 Workharu <Workharu@users.noreply.github.com> - 0.2.0-1
- Phase 2 release: PyPI + .deb + .rpm distribution.
- Bundled python3.11 venv at /opt/eth-validator-stats/venv.
- Adds eth-validator-stats init --system flag for system-service onboarding.
