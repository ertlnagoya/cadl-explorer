"""Japanese for the texts that `cadl norms-check` writes in a fixed form.

cadl writes its notes and the explanations of its witnesses in English. The
forms below are those of cadl/norms_check.py (0.3.43); each is matched as a
whole, and a text in another form, such as one of a later cadl, is shown as
cadl wrote it. `note`, `claim` and `explanation` return the text unchanged
unless the reader chose Japanese.
"""

from __future__ import annotations

import re
from typing import Callable, List, Tuple, Union

from views._i18n import japanese

_REASONS = {
    "a product of two quantities": "2 つの量の積",
    "a division by a quantity": "量による割り算",
    "too many alternatives": "場合分けが多すぎる",
}


def _reason(text: str) -> str:
    m = re.fullmatch(r"(\w+) in arithmetic", text)
    return f"算術の中の {m[1]}" if m else _REASONS.get(text, text)

Rule = Tuple["re.Pattern[str]", Union[str, Callable[["re.Match[str]"], str]]]


def _nonlinear(m: "re.Match[str]") -> str:
    reason = _reason(m["why"])
    return (f"{m['expr']} は線形ではありません（{reason}）。{m['names']} は {m['lo']}..{m['hi']} "
            f"の整数をとるものとしました。この定義域は，述語のリテラルから仮定したものです"
            f"（--domain {m['domain']} と同じ）。答えは，これらの数がそのような整数である"
            f"トレースについてだけ成り立ちます。別の定義域にするには --domain を指定してください")


_NOTES: List[Rule] = [
    (re.compile(r"index (?P<v>\S+) is not bound; it is bound to (?P<lo>\S+) "
                r"\(use --bindings \S+=n\)"),
     "添字 {v} が束縛されていないので，{lo} に束縛しました（--bindings {v}=n で指定できます）"),
    (re.compile(r"--members (?P<s>\S+)=(?P<n>\d+): \S+ is declared with (?P<all>\d+) members; "
                r"the answers \(\"violable: no\" among them\) hold for instances with \d+ of "
                r"them only"),
     "--members {s}={n}：{s} は {all} 個の構成員で宣言されています。答え（「違反しうる：いいえ」"
     "を含む）は，そのうち {n} 個の構成員をもつインスタンスについてだけ成り立ちます"),
    (re.compile(r"(?P<s>\S+)\[(?P<lo>[^\]\s]+)\.\.(?P<hi>[^\]\s]+)\] has (?P<n>\d+) member\(s\) "
                r"in the instance because --members does not give its size; the answers hold "
                r"for that size only: give --members \S+=n to check more"),
     "--members が {s} の大きさを指定していないので，インスタンスでは {s}[{lo}..{hi}] の構成員を "
     "{n} 個としました。答えはこの大きさについてだけ成り立ちます。ほかの大きさを調べるには "
     "--members {s}=n を指定してください"),
    (re.compile(r"--bindings (?P<v>\S+): the contract does not use the name \S+; the binding "
                r"has no effect"),
     "--bindings {v}：契約は名前 {v} を使っていないので，この束縛は効きません"),
    (re.compile(r"the index of (?P<ref>.+) is not bound: it matches every member, as in "
                r"norms-eval"),
     "{ref} の添字が束縛されていないので，norms-eval と同じく，すべての構成員に当てはまります"),
    (re.compile(r"(?P<s>\S+)\[(?P<lo>[^\]\s]+)\.\.(?P<hi>[^\]\s]+)\] belong to the domain of a "
                r"quantifier or aggregate but not to the instance \(--members\): in the model "
                r"they have no value"),
     "{s}[{lo}..{hi}] は量化子か集約の範囲に入りますが，インスタンス（--members）には入りません。"
     "モデルでは，これらは値をもちません"),
    (re.compile(r"(?P<expr>.+) is not linear \((?P<why>[^()]+)\): (?P<names>.+?) takes? the "
                r"integers (?P<lo>-?\d+)\.\.(?P<hi>-?\d+), a domain assumed from the literals of "
                r"the predicate \(as --domain (?P<domain>\S+)\); the answers hold only for traces "
                r"whose numbers for (?:it|them) are such integers\. Give --domain to choose "
                r"another one"),
     _nonlinear),
    (re.compile(r"the (?P<n>\d+) instances start at observations that the environment chooses, "
                r"in any order, and share the observations, the message events and the "
                r"quantities; each ends at an observation of its own"),
     "{n} 個のインスタンスは，環境が選ぶ観測で順不同に始まり，観測，メッセージのイベント，量を"
     "共有します。各インスタンスは，それぞれの観測で終わります"),
    (re.compile(r"the (?P<n>\d+) instances start together at the first observation "
                r"\(--starts together\) and share the observations, the message events and the "
                r"quantities; each ends at an observation of its own \(traces whose instances "
                r"start at different observations are not covered\)"),
     "{n} 個のインスタンスは最初の観測でそろって始まり（--starts together），観測，メッセージの"
     "イベント，量を共有します。各インスタンスは，それぞれの観測で終わります（インスタンスが"
     "異なる観測で始まるトレースは対象外です）"),
    (re.compile(r"an instance that starts at observation s has at most (?P<h>\d+) - s \+ 1 "
                r"observations \(the horizon \d+ is shared\): a later start leaves a shorter "
                r"instance, and a larger --horizon covers longer ones"),
     "観測 s で始まるインスタンスの観測は，最大で {h} - s + 1 個です（時間の範囲 {h} を共有します）。"
     "遅く始まるほどインスタンスは短くなります。長いインスタンスを調べるには --horizon を大きくして"
     "ください"),
    (re.compile(r"(?P<q>\S+) takes the integers (?P<lo>-?\d+)\.\.(?P<hi>-?\d+) of --domain and "
                r"the values that are not numbers: the answers hold for traces in which its "
                r"numbers are such integers"),
     "{q} は，--domain の {lo}..{hi} の整数と，数でない値をとります。答えは，その数がそのような"
     "整数であるトレースについて成り立ちます"),
]

_CLAIMS = {
    "activated": "{n} が発効する",
    "violated": "{n} に違反する",
    "fulfilled": "{n} が果たされる",
    "exercised": "{n} が行使される",
    "reparation": "{n} の補償が使われる",
}

_EXPLANATIONS: List[Rule] = [
    (re.compile(r"replayed from the strategy of PRISM"), "PRISM の戦略から再現したトレース"),
    (re.compile(r"the strategy did not reach the state"), "戦略はその状態に到達しませんでした"),
    (re.compile(r"every instance that runs to the horizon violates the norm; this is the one "
                r"without messages and without values"),
     "時間の範囲の終わりまで動くすべてのインスタンスがこの規範に違反します。これは，メッセージも"
     "値もないインスタンスです"),
    (re.compile(r"at observation (?P<k>\d+) every choice of the environment that activates the "
                r"norm leads to a state from which every continuation that runs to the horizon "
                r"violates it; this trace takes one of them and continues without messages and "
                r"values"),
     "観測 {k} で，規範を発効させる環境の選択はどれも，時間の範囲の終わりまで動く続きがすべて"
     "違反する状態に至ります。このトレースはそのうちの 1 つをとり，メッセージも値もなしに続けます"),
    (re.compile(r"after (?P<k>\d+) observation\(s\) both norms are in force; keeping (?P<keep>\S+) "
                r"violates (?P<other>\S+)"),
     "観測 {k} 回の後，2 つの規範がともに効力をもちます。{keep} を守ると {other} に違反します"),
]


def _apply(rules: List[Rule], text: str) -> str:
    for pattern, out in rules:
        m = pattern.fullmatch(text.strip())
        if m:
            return out(m) if callable(out) else out.format(**m.groupdict())
    return text


def note(text: str) -> str:
    """A note of norms-check, in Japanese when its form is known."""
    return _apply(_NOTES, str(text)) if japanese() else str(text)


def claim(text: str) -> str:
    """The claim of a witness, `<norm> <activated|violated|...>`."""
    text = str(text)
    if not japanese():
        return text
    norm, _, word = text.rpartition(" ")
    return _CLAIMS[word].format(n=norm) if norm and word in _CLAIMS else text


def explanation(text: str) -> str:
    """The explanation of a witness, in Japanese when its form is known."""
    return _apply(_EXPLANATIONS, str(text)) if japanese() else str(text)
