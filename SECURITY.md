# Security and safety

NORI MotorLab directly controls physical actuators. Treat access to its web
interface as access to the connected hardware.

- The server binds to `127.0.0.1` by default and has no authentication.
- Non-loopback binding requires the explicit `--allow-remote` acknowledgement.
  Do not use it on a public interface or untrusted network.
- Browser WebSockets are restricted to the page's own origin, and HTTP
  responses include a restrictive local-app security policy. State-changing
  API requests also require a non-simple request header to block cross-site
  form submissions.
- Use a current-limited external supply and a physical power disconnect.
- Review and test changes to motion, EEPROM, ID, or encoder-center paths with
  simulation before connecting hardware.
- Do not include serial numbers, recordings, local paths, access tokens, or
  other sensitive bench data in bug reports.

Report security-sensitive issues through GitHub's **Report a vulnerability**
control on the repository Security tab when it is available. Otherwise, open
a minimal issue asking the maintainers for a private reporting channel without
including vulnerability details. Include the affected revision, impact,
reproduction steps, and any safe mitigation in the private report.
