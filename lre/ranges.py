"""Parse the reference ranges labs print: '12.0-15.0', '<40', 'up to 0.9', '<200: Desirable', 'Adults: 0.27-4.20'."""
import re
from dataclasses import dataclass

NUM = r"(\d+(?:\.\d+)?)"


@dataclass(frozen=True)
class Interval:
    lo: float | None = None
    hi: float | None = None
    lo_incl: bool = True
    hi_incl: bool = True

    def contains(self, v: float) -> bool:
        if self.lo is not None and (v < self.lo or (v == self.lo and not self.lo_incl)):
            return False
        if self.hi is not None and (v > self.hi or (v == self.hi and not self.hi_incl)):
            return False
        return True

    def scaled(self, f: float) -> "Interval":
        s = lambda x: None if x is None else round(x * f, 6)
        return Interval(s(self.lo), s(self.hi), self.lo_incl, self.hi_incl)

    def text(self) -> str:
        g = lambda x: f"{x:g}"
        if self.lo is not None and self.hi is not None:
            return f"{g(self.lo)}-{g(self.hi)}"
        if self.hi is not None:
            return ("<=" if self.hi_incl else "<") + g(self.hi)
        return (">=" if self.lo_incl else ">") + g(self.lo)


def _clean(t: str) -> str:
    t = t.replace("–", "-").replace("—", "-").replace("=<", "<=").replace("=>", ">=").replace("≤", "<=").replace("≥", ">=")
    t = re.sub(r"\bupto\b", "up to", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def parse_interval(text: str) -> Interval | None:
    t = _clean(text or "")
    if m := re.search(r"(?<![\d.<>=])" + NUM + r"\s*-\s*" + NUM, t):
        return Interval(float(m[1]), float(m[2]))
    if m := re.search(r"(<=|<|up to)\s*" + NUM, t, re.I):
        return Interval(None, float(m[2]), hi_incl=m[1] != "<")
    if m := re.search(r"(>=|>)\s*" + NUM, t):
        return Interval(float(m[2]), None, lo_incl=m[1] == ">=")
    if m := re.fullmatch(NUM, t):
        return Interval(None, float(m[1]))
    return None


def split_range(label: str, text: str) -> tuple[str, str]:
    """Labs print both '<200: Desirable' and 'Non-Diabetic: <=5.6'. Return (label, interval_text)."""
    if label or ":" not in text:
        return label.strip(), text.strip()
    left, right = (s.strip() for s in text.split(":", 1))
    if parse_interval(left) and re.search(r"[A-Za-z]", right) and not parse_interval(right):
        return right, left
    return left, right


_TRI = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3}


def classify_label(label: str) -> dict:
    l = _clean(label)
    low = l.lower()
    if m := re.search(r"(1st|2nd|3rd|first|second|third)\s*trimester", low):
        return {"kind": "trimester", "n": _TRI[m[1]]}
    section = None
    if re.search(r"non[- ]?pregnant", low):
        section = "nonpregnant"
    elif "pregnan" in low:
        section = "pregnant"
    rest = re.sub(r"(?i)non[- ]?pregnant|pregnant", "", l).strip(" -:,")
    if section and not rest:
        return {"kind": section}
    if section is None and re.search(r"\b(years?|yrs?)\b", low):
        nums = [float(x) for x in re.findall(NUM, l)]
        if len(nums) >= 2:
            return {"kind": "age", "age": Interval(nums[0], nums[1])}
        if len(nums) == 1:
            return {"kind": "age", "age": Interval(nums[0], None) if l.lstrip().startswith(">") else Interval(None, nums[0])}
    if section is None and "adult" in low:
        return {"kind": "adult"}
    if rest:
        return {"kind": "band", "band": rest, "section": section}
    return {"kind": "plain"}
