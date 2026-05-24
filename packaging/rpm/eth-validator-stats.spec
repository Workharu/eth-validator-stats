Name:           eth-validator-stats
Version:        0.2.0
Release:        1%{?dist}
Summary:        Ethereum validator stats watcher

License:        MIT
URL:            https://github.com/Workharu/eth-validator-stats
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3.11
BuildRequires:  python3.11-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  tar

Requires:       python3.11
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
mkdir -p %{buildroot}/opt/%{name} \
         %{buildroot}%{_bindir} \
         %{buildroot}%{_unitdir} \
         %{buildroot}/etc/%{name} \
         %{buildroot}/var/lib/%{name}

# Build the venv at the final runtime path so shebangs resolve.
python3.11 -m venv %{buildroot}/opt/%{name}/venv
%{buildroot}/opt/%{name}/venv/bin/pip install --no-cache-dir .

# Rewrite shebangs in pip-installed scripts so they point at /opt/%{name}/...
# instead of the buildroot.
find %{buildroot}/opt/%{name}/venv/bin -type f -executable \
    -exec sed -i "s|^#!%{buildroot}|#!|g" {} +

# Rewrite buildroot path references in pyvenv.cfg (the `executable` and
# `command` lines record the absolute path used to create the venv).
sed -i "s|%{buildroot}||g" %{buildroot}/opt/%{name}/venv/pyvenv.cfg

# Drop activation scripts: they're not needed at runtime (the service
# ExecStart calls the venv binary directly) and they embed the buildroot
# path in VIRTUAL_ENV=..., which would cause check-buildroot to abort.
rm -f %{buildroot}/opt/%{name}/venv/bin/activate \
      %{buildroot}/opt/%{name}/venv/bin/activate.csh \
      %{buildroot}/opt/%{name}/venv/bin/activate.fish \
      %{buildroot}/opt/%{name}/venv/bin/activate.nu \
      %{buildroot}/opt/%{name}/venv/bin/activate.ps1

# Strip __pycache__ / .pyc to keep the package small.
find %{buildroot}/opt/%{name}/venv -type d -name '__pycache__' -prune -exec rm -rf {} +
find %{buildroot}/opt/%{name}/venv -name '*.pyc' -delete

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
/opt/%{name}/venv
%{_bindir}/%{name}
%{_unitdir}/%{name}.service
%dir %attr(0750, eth-validator-stats, eth-validator-stats) /etc/%{name}
%dir %attr(0750, eth-validator-stats, eth-validator-stats) /var/lib/%{name}

%changelog
* Sun May 24 2026 Workharu <Workharu@users.noreply.github.com> - 0.2.0-1
- Phase 2 release: PyPI + .deb + .rpm distribution.
- Bundled python3.11 venv at /opt/eth-validator-stats/venv.
- Adds eth-validator-stats init --system flag for system-service onboarding.
