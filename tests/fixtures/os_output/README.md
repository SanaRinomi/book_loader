# OS command output

Inputs for the MAC address parsers (T0.4.3, T4.2.2).

| File | Source |
|---|---|
| `getmac_captured.txt` | `getmac /fo csv /nh /v`, captured on a Spanish-language Windows 11 machine, console code page bytes |
| `get_netadapter_captured.txt` | `Get-NetAdapter \| Select MacAddress` in Windows PowerShell 5.1 |
| `get_netadapter_csv_captured.txt` | `Get-NetAdapter \| Select-Object Name, Status, MacAddress \| ConvertTo-Csv -NoTypeInformation` |
| `ifconfig_macos_handwritten.txt` | Written by hand from documented `ifconfig -a` output on macOS |
| `sys_class_net_linux_handwritten.txt` | Written by hand: `<interface> <address>` per line, from `/sys/class/net/*/address` |

Captured files come from `uv run python -m tests.tools.make_fixtures os_output`, which
replaces every real MAC address and GUID with a fake one (`02:00:5E:10:00:nn`,
`00000000-0000-4000-8000-...`) before writing. MAC addresses are Kobo key material, so
real ones never go into the repository. The all-zero address is kept.

The hand-written macOS and Linux files are replaced with real captures once those
systems are available (deferred item D6 in REFACTOR_ACTION_PLAN.md). Run the same
command there; it writes `ifconfig_macos_captured.txt` or `sys_class_net_linux_captured.txt`.
