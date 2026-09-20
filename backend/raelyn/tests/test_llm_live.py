from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.services.llm import llm_enabled


# 在这里配置“待优化转写内容”（稳定复现，不依赖任何环境变量）。
POLISH_TEST_TEXT = """
也许这才是真正的历史转折点。这个转折不是那些政治家在台上喊的口号，也不是发下来没人细读的红头文件，而是发生在2026年二月26日的这天。推特创始人杰克多尔西旗下的Block公司首期刀落一口气裁掉了40%的员工，一万人的大公司直接砍到不到六千，四千多人前一天还在岗位上喝咖啡，第二天就抱着纸箱站在街头。关键是这家公司可不是什么快断气的夕阳企业。恰恰相反，他刚刚发布的财报显示，利润增长了24%，业务增长了33%，远超华尔街那些精英分析师的预期。这是一家躺在钱堆上数钱的公司，是一家根本不缺钱的公司，但他就是要裁，而且要一次性裁掉近一半。到底为什么？杰克多尔西在那份公开信里说得很明白：AI彻底改变了游戏规则，更小的团队配上AI工具，不仅能做更多的事，而且能做得更好。AI的能力每周都在指数级增长，那些重复性的、流程化的、做在办公室里就能完成的工作，已经没有必要让人去做了。消息出，资本市场的反应让所有人都倒吸口凉气。Block的股价在盘后暴涨了23%。各位看懂了吗？这才是最恐怖的地方。以前我们说裁员，那肯定是公司不行了，多币求生，保命要紧。股价呢肯定暴跌，因为说明公司出大问题了。但这次完全反过来了，资本市场用真金白银投票告诉你，裁掉人类员工对企业来说是重大利好。人少了，成本低了，效率反而高了，利润自然就上去了。一家赚钱的公司主动选择砍掉近一半的人，而且股价暴涨，这在人类商业史上恐怕是头一遭。而Block这一刀很可能只是序幕。真正让人脊背发凉的，是那份在国内外投资圈引起地震的报告——二零二八全球智能危机。这份报告来自一个名不见经传的小型投资机构，作者是两个看起来没什么影响力的年轻人。但报告发布后，让整个华尔街炸锅了。白宫亲自下场批驳，说是一篇有趣的科幻作品。黑天鹅之父塔勒布也力挺，说市场严重低估了风险。这份报告到底说了什么呢？它构建了一个环环相扣的崩溃链条，不是简单的AI抢工作那种陈词滥调，而是一个结构性、利率会飙升，社会体系崩溃，贫富差距达到极值，大量人口陷入动荡。乐观者的叙事是 AI 是新的生产力革命，类似上世纪90年代的互联网。汽车发明的时候，马车夫确实失业了，但创造了整个汽车产业和物流行业。蒸汽机出现的时候，纺织工人确实下岗了，但催生了工业革命。技术进步总是先破坏后建设，最终会创造比摧毁更多的机会，让所有人受益。这两个叙事目前都在争夺解释权。那么普通人该怎么办？先说一个残酷的事实，历史上每一次技术革命确实最终都创造了更多就业，但这里的最终可能需要一代人的时间。而在转型期内被碾碎的恰恰是那一代人。蒸汽机出现后，英国工人的实际工资下降了半个世纪。电气革命期间，大量的手工业者失去了生计，直到新的工业体系完全建立。这一次情况可能更严重，因为以前的机器替代的是体力，人类还有脑力可以出卖，而现在 AI 替代的就是脑力。Block 的裁员已经证明，哪怕是科技公司，哪怕是高技能岗位，哪怕是利润增长期的企业，也照样可以一夜之间裁掉40%。所以不要再幻想什么稳定的工作，这个时代稳定本身就是最大的风险。要么成为能指挥 AI 的人，知道怎么让 AI 为你干活；要么成为能创造 AI 的人，去写代码、做算法、搞研发；要么就必须去做那些 AI 搞不定的事儿，搞定人性，做需要情感共鸣、需要身体在场、需要复杂应变的工作。真正的问题从来不是 AI 会不会替代我，而是我能不能利用 AI 让自己升级。要么进化，要么被淘汰，中间地带正在迅速消失。这就是2026年开年 Block 公司用4000个岗位的代价给人们上的最残酷的一课。
""".strip()

# 增大该值可模拟更长文本，触发 `_polish_transcript_via_llm` 的分段（每段 12_000 chars）。
POLISH_TEST_REPEAT = 1

# 若要直接判定“是否异常慢”，可以设置阈值（秒）；否则设为 None 仅打印耗时。
MAX_SECONDS_POLISH: float | None = None


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_LLM_LIVE") == "1",
    "设置 RAELYN_RUN_LLM_LIVE=1 后调用已配置的真实 LLM",
)
class LiveTranscriptPolishTests(unittest.TestCase):
    def test_polish_transcript_via_llm_returns_and_prints_latency(self) -> None:
        if not llm_enabled():
            self.skipTest("LLM_URL is not configured")

        # Directly calls the real implementation and its original prompt/processing.
        from raelyn.jobs.handlers import _polish_transcript_via_llm, _split_text_for_llm  # noqa: PLC0415

        text = (POLISH_TEST_TEXT or "").strip()
        if POLISH_TEST_REPEAT > 1:
            text = ((text + "\n") * POLISH_TEST_REPEAT).strip()
        if not text:
            self.skipTest("POLISH_TEST_TEXT is empty")

        url = settings.llm_url.strip()
        model = settings.llm_model.strip()
        timeout = int(getattr(settings, "llm_timeout_seconds", 0) or 0)

        chunks = _split_text_for_llm(text, max_chars=12_000)
        start = time.perf_counter()
        try:
            out, usage = _polish_transcript_via_llm(session=None, text=text)
            elapsed = time.perf_counter() - start
            out_s = (out or "").strip()
            print(
                f"[live-polish] ok; {elapsed:.3f}s; in={len(text)} chars; out={len(out_s)} chars; "
                f"chunks={len(chunks)}; input_tokens={usage.get('input_tokens', 0)}; "
                f"output_tokens={usage.get('output_tokens', 0)}; timeout={timeout}s; url={url!r} model={model!r}"
            )
            self.assertTrue(out_s)
            if MAX_SECONDS_POLISH is not None:
                self.assertLessEqual(elapsed, MAX_SECONDS_POLISH, f"polish call took too long: {elapsed:.3f}s")
        except Exception as e:
            elapsed = time.perf_counter() - start
            print(
                f"[live-polish] failed; {elapsed:.3f}s; in={len(text)} chars; chunks={len(chunks)}; "
                f"timeout={timeout}s; url={url!r} model={model!r}; err={type(e).__name__}: {e}"
            )
            raise


if __name__ == "__main__":
    unittest.main()
