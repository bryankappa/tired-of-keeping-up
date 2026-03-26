from __future__ import annotations


def apply_twscrape_patch() -> None:
    try:
        import twscrape.xclid as xclid
    except ImportError:
        return

    if getattr(xclid, "_x_signal_engine_patched", False):
        return

    def _rextr(s: str, begin: str, end: str, pos: int) -> str | None:
        end_idx = s.rfind(end, 0, pos)
        if end_idx < 0:
            return None
        begin_idx = s.rfind(begin, 0, end_idx)
        if begin_idx < 0:
            return None
        return s[begin_idx + len(begin) : end_idx]

    def _fextr(s: str, begin: str, end: str, pos: int = 0) -> str | None:
        start = s.find(begin, pos)
        if start < 0:
            return None
        start += len(begin)
        stop = s.find(end, start)
        if stop < 0:
            return None
        return s[start:stop]

    async def _patched_parse_anim_idx(text: str) -> list[int]:
        # X changed the client chunk map format in March 2026.
        ondemand_pos = text.find('"ondemand.s"')
        if ondemand_pos >= 0:
            ondemand_key = _rextr(text, ",", ":", ondemand_pos)
            if ondemand_key:
                ondemand_suffix = _fextr(text, ondemand_key + ':"', '"', ondemand_pos)
                if ondemand_suffix:
                    url = xclid.script_url("ondemand.s", f"{ondemand_suffix}a")
                    js_text = await xclid.get_tw_page_text(url)
                    items = [int(match.group(2)) for match in xclid.INDICES_REGEX.finditer(js_text)]
                    if items:
                        return items

        scripts = list(xclid.get_scripts_list(text))
        scripts = [url for url in scripts if "/ondemand.s." in url]
        if not scripts:
            raise Exception("Couldn't get XClientTxId scripts")

        js_text = await xclid.get_tw_page_text(scripts[0])
        items = [int(match.group(2)) for match in xclid.INDICES_REGEX.finditer(js_text)]
        if not items:
            raise Exception("Couldn't get XClientTxId indices")
        return items

    xclid.parse_anim_idx = _patched_parse_anim_idx
    xclid._x_signal_engine_patched = True
