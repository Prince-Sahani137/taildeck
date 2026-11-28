# Security policy

TailDeck gives authenticated users a shell and desktop access to the machine
it runs on, so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private
"Report a vulnerability" button on the repository's Security tab. Include
steps to reproduce and the affected version or commit.

## Deployment guidance

- Expose TailDeck only through Tailscale (`tailscale serve`), never directly to
  the internet or your LAN.
- Use a long random `PANEL_PASSWORD` and keep Tailscale ACLs tight.
- Keep `.env` private (`chmod 600 .env`) and out of version control.
