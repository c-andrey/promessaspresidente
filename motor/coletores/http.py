import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def cliente(timeout: float = 30, proxy: str | None = None) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, proxy=proxy,
                        headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
