# Disable the debugsource / debuginfo subpackages. Our package bundles a
# pre-built Python venv; there are no source files for RPM to extract debug
# symbols from, and Fedora's default policy would otherwise fail with
# "Empty %files file ... debugsourcefiles.list".
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

Name:           eth-validator-stats
Version:        0.3.0
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

# Symlink /usr/bin entrypoint.
ln -sf /opt/%{name}/venv/bin/%{name} %{buildroot}%{_bindir}/%{name}

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
chmod 0750 /etc/%{name} /var/lib/%{name}
%systemd_post %{name}.service

if [ $1 -eq 1 ] ; then
    cat <<'EOF'

eth-validator-stats installed.

Next steps:
  sudo eth-validator-stats init --system
  sudo systemctl start eth-validator-stats

EOF
fi

%preun
%systemd_preun %{name}.service

%postun
%systemd_postun_with_restart %{name}.service

%files
%license LICENSE
%doc README.md
%dir /opt/%{name}
/opt/%{name}/python
/opt/%{name}/venv
%{_bindir}/%{name}
%{_unitdir}/%{name}.service
# The config and state directories are intentionally NOT listed here:
# the post-install scriptlet creates them so the package does not own the
# dirs (preserving any user-created contents on uninstall).

%changelog
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
