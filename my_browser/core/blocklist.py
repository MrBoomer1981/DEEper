"""Sites the user never wants as sources; core keeps the list and hands it to each research."""

from my_browser.common.settings import REPORTS_DIR
from my_browser.common.text import host

BLOCKLIST_PATH = REPORTS_DIR / "blocked_domains.txt"  # on Railway this lives on the volume


def blocked_domains() -> list[str]:
    if not BLOCKLIST_PATH.exists():
        return []
    return [line.strip().lower() for line in BLOCKLIST_PATH.read_text("utf-8").splitlines() if line.strip()]


def block(domain: str) -> str:
    domain = host(domain if "://" in domain else f"https://{domain}")
    if domain and domain not in blocked_domains():
        BLOCKLIST_PATH.parent.mkdir(exist_ok=True)
        with BLOCKLIST_PATH.open("a", encoding="utf-8") as f:
            f.write(domain + "\n")
    return domain


def unblock(domain: str) -> bool:
    domain = host(domain if "://" in domain else f"https://{domain}")
    current = blocked_domains()
    if domain not in current:
        return False
    BLOCKLIST_PATH.write_text("".join(d + "\n" for d in current if d != domain), "utf-8")
    return True
