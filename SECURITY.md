# Security and safety

Feetech MotorLab directly controls physical actuators. Treat access to its web
interface as access to the connected hardware.

- The server binds to `127.0.0.1` by default and has no authentication.
- Do not bind to a public interface or untrusted network.
- Use a current-limited external supply and a physical power disconnect.
- Review and test changes to motion, EEPROM, ID, or encoder-center paths with
  simulation before connecting hardware.
- Do not include serial numbers, recordings, local paths, access tokens, or
  other sensitive bench data in bug reports.

Report security-sensitive issues privately to the repository maintainers
rather than opening a public issue. Include the affected revision, impact,
reproduction steps, and any safe mitigation you have identified.
