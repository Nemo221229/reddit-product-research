"""M3: render report_data.json into the approved report-page design (data-driven).

Output is Artifact-style HTML (no <html>/<head>/<body> wrapper) that also opens
standalone in a browser. Reuses the prototype's tokens/CSS so the real report
looks identical to the approved形态.

  python build_report.py "<keyword>"              # -> <workdir>/reports/<slug>.html
  python build_report.py "<keyword>" out.html     # custom output path
"""
import json, sys
from pathlib import Path

from paths import REPORTS, slugify

if len(sys.argv) < 2:
    raise SystemExit('用法: python build_report.py "<keyword>" [out.html]')
_slug = slugify(sys.argv[1])
REPORT = json.load(open(REPORTS / f"report_data_{_slug}.json", encoding="utf-8"))
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else REPORTS / f"{_slug}.html"

TEMPLATE = r"""<title>__KW__ · Reddit 选品情报报告</title>
<style>
  :root{color-scheme:light;
    --plane:#f7f5f1;--surface:#fffdf8;--surface-2:#f2efe8;--ink:#1a1712;--ink-2:#57534a;
    --muted:#8a857a;--hair:#e5e1d6;--hair-2:#d8d3c6;--accent:#d9481f;--accent-ink:#b23a17;
    --pos:#0ca30c;--pos-ink:#0a7a0a;--mix:#d98a00;--mix-ink:#b06f00;--neg:#d03b3b;--neg-ink:#b32f2f;--brand-ink:#1f5fbf;
    --grid:#ece8de;--shadow:0 1px 2px rgba(26,23,18,.05),0 6px 24px -12px rgba(26,23,18,.14);
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;}
  @media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;
    --plane:#131110;--surface:#1c1a17;--surface-2:#24211c;--ink:#f6f2e9;--ink-2:#b8b2a4;--muted:#8f897c;
    --hair:#302c26;--hair-2:#3b362e;--accent:#f26a3d;--accent-ink:#f7855e;--pos:#22b322;--pos-ink:#4cc94c;
    --mix:#f0a52a;--mix-ink:#f4b957;--neg:#e15656;--neg-ink:#ec7a7a;--brand-ink:#7fb0ff;--grid:#262219;
    --shadow:0 1px 2px rgba(0,0,0,.3),0 8px 30px -14px rgba(0,0,0,.6);}}
  :root[data-theme="dark"]{color-scheme:dark;--plane:#131110;--surface:#1c1a17;--surface-2:#24211c;
    --ink:#f6f2e9;--ink-2:#b8b2a4;--muted:#8f897c;--hair:#302c26;--hair-2:#3b362e;--accent:#f26a3d;
    --accent-ink:#f7855e;--pos:#22b322;--pos-ink:#4cc94c;--mix:#f0a52a;--mix-ink:#f4b957;--neg:#e15656;
    --neg-ink:#ec7a7a;--brand-ink:#7fb0ff;--grid:#262219;--shadow:0 1px 2px rgba(0,0,0,.3),0 8px 30px -14px rgba(0,0,0,.6);}
  *{box-sizing:border-box}
  body{margin:0;background:var(--plane);color:var(--ink);font-family:var(--sans);line-height:1.55;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:0 24px}
  .eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:0}
  .mast{padding:40px 0 26px;border-bottom:1.5px solid var(--ink)}
  .mast-top{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px 20px}
  .brand{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-2);font-weight:600}
  .brand b{color:var(--accent)}
  .doc-id{font-family:var(--mono);font-size:12px;color:var(--muted)}
  h1.title{font-size:clamp(34px,6vw,64px);line-height:1;letter-spacing:-.03em;font-weight:800;margin:22px 0 4px;text-wrap:balance}
  h1.title .kw{color:var(--accent)} h1.title .kw::before{content:"「";color:var(--hair-2);font-weight:400} h1.title .kw::after{content:"」";color:var(--hair-2);font-weight:400}
  .subtitle{font-size:16px;color:var(--ink-2);margin:12px 0 0;max-width:62ch}
  .meta{display:flex;flex-wrap:wrap;margin-top:26px;border:1px solid var(--hair);border-radius:10px;overflow:hidden;background:var(--surface)}
  .meta div{flex:1 1 120px;padding:12px 16px;border-right:1px solid var(--hair)} .meta div:last-child{border-right:0}
  .meta .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
  .meta .v{font-size:19px;font-weight:700;margin-top:3px;font-variant-numeric:tabular-nums}
  .meta .v small{font-size:12px;font-weight:500;color:var(--ink-2)}
  section{padding:44px 0;border-bottom:1px solid var(--hair)}
  .sec-head{display:flex;align-items:baseline;gap:14px;margin-bottom:22px}
  .sec-num{font-family:var(--mono);font-size:12px;color:var(--accent);font-weight:600}
  h2{font-size:25px;letter-spacing:-.02em;font-weight:750;margin:0}
  .sec-sub{color:var(--muted);font-size:14px;margin:4px 0 0}
  .summary{font-size:clamp(18px,2.4vw,23px);line-height:1.55;font-weight:500;letter-spacing:-.01em;max-width:64ch;text-wrap:pretty}
  .summary .hi{background:linear-gradient(transparent 62%,color-mix(in srgb,var(--accent) 26%,transparent) 62%);padding:0 1px}
  .summary .neg-hi{color:var(--neg-ink);font-weight:700}
  .takeaways{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:30px}
  .takeaway{background:var(--surface);border:1px solid var(--hair);border-radius:12px;padding:18px;box-shadow:var(--shadow)}
  .takeaway .tag{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase}
  .takeaway .big{font-size:28px;font-weight:800;letter-spacing:-.02em;margin:6px 0 2px}
  .takeaway p{font-size:13.5px;color:var(--ink-2);margin:4px 0 0}
  .t-op .tag{color:var(--accent)} .t-pain .tag{color:var(--neg-ink)} .t-rise .tag{color:var(--pos-ink)}
  .quad-legend{display:flex;flex-wrap:wrap;gap:8px 20px;margin-bottom:18px;align-items:center}
  .legend-item{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;color:var(--ink-2)}
  .legend-item .dot{width:12px;height:12px;border-radius:50%}
  .quad-figure{background:var(--surface);border:1px solid var(--hair);border-radius:14px;padding:8px;box-shadow:var(--shadow);overflow:hidden}
  svg{display:block;width:100%;height:auto}
  .quad-note{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px}
  .callout{flex:1 1 260px;display:flex;gap:11px;padding:13px 15px;border-radius:11px;background:var(--surface-2);border:1px solid var(--hair)}
  .callout .mark{font-family:var(--mono);font-weight:700;font-size:13px;color:var(--accent);white-space:nowrap}
  .callout p{margin:0;font-size:13px;color:var(--ink-2)} .callout p b{color:var(--ink)}
  .two-col{display:grid;grid-template-columns:1.35fr 1fr;gap:24px;align-items:start}
  .panel{background:var(--surface);border:1px solid var(--hair);border-radius:14px;padding:22px;box-shadow:var(--shadow)}
  .panel>.eyebrow{margin-bottom:14px}
  .cloud{position:relative;width:100%;height:360px;margin:0 auto;overflow:hidden}
  .cloud span{position:absolute;font-weight:800;letter-spacing:-.02em;cursor:default;white-space:nowrap;line-height:1;transform:translate(-50%,-50%);transition:opacity .12s ease}
  .cloud:hover span{opacity:.32} .cloud span:hover{opacity:1}
  .s-pos{color:var(--pos-ink)} .s-mix{color:var(--mix-ink)} .s-neg{color:var(--neg-ink)} .s-neu{color:var(--ink-2)} .s-accent{color:var(--accent-ink)} .s-brand{color:var(--brand-ink)} .s-pain{color:var(--neg-ink)} .s-feat{color:var(--ink-2)}
  .cloud-key{display:flex;gap:16px;margin-top:16px;padding-top:14px;border-top:1px solid var(--hair);flex-wrap:wrap}
  .cloud-key span{font-family:var(--mono);font-size:11px;display:inline-flex;align-items:center;gap:6px}
  .cloud-key i{width:9px;height:9px;border-radius:50%}
  .sent-row{display:flex;align-items:center;gap:12px;margin-bottom:15px}
  .sent-row .name{flex:0 0 110px;font-size:12.5px;color:var(--ink);font-weight:600}
  .sent-bar{flex:1;height:20px;border-radius:5px;overflow:hidden;display:flex;gap:2px;background:var(--surface-2)}
  .sent-bar i{height:100%} .sent-bar .p{background:var(--pos)} .sent-bar .m{background:var(--mix)} .sent-bar .n{background:var(--neg)}
  .sent-row .score{flex:0 0 auto;font-family:var(--mono);font-size:12px;width:42px;text-align:right}
  .cards{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
  .card{background:var(--surface);border:1px solid var(--hair);border-radius:14px;padding:20px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:13px}
  .card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
  .card-rank{font-family:var(--mono);font-size:12px;color:var(--muted);font-weight:600}
  .card h3{font-size:19px;margin:3px 0 0;letter-spacing:-.01em;font-weight:750}
  .card .en{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-top:2px}
  .zone-badge{font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;padding:4px 8px;border-radius:20px;white-space:nowrap;font-weight:600}
  .z-op{background:color-mix(in srgb,var(--accent) 15%,transparent);color:var(--accent-ink)}
  .z-red{background:color-mix(in srgb,var(--neg) 14%,transparent);color:var(--neg-ink)}
  .z-blue{background:color-mix(in srgb,var(--pos) 14%,transparent);color:var(--pos-ink)}
  .z-tail{background:var(--surface-2);color:var(--muted)}
  .metrics{display:flex;gap:18px;flex-wrap:wrap}
  .metric .ml{font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
  .metric .mv{font-size:17px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:1px}
  .oppbar-wrap{display:flex;align-items:center;gap:10px}
  .oppbar{flex:1;height:6px;border-radius:3px;background:var(--surface-2);overflow:hidden}
  .oppbar i{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,color-mix(in srgb,var(--accent) 45%,var(--mix)),var(--accent))}
  .opplabel{font-family:var(--mono);font-size:11px;color:var(--ink-2);white-space:nowrap;font-weight:600} .opplabel b{color:var(--accent-ink)}
  .senttag{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;font-family:var(--mono)}
  .senttag i{width:8px;height:8px;border-radius:50%}
  .card-note{font-family:var(--mono);font-size:11px;color:var(--muted)}
  .evidence{background:var(--surface-2);border:1px solid var(--hair);border-radius:9px;padding:10px 12px;font-size:12px;color:var(--ink-2);line-height:1.5}
  .evidence .el{font-family:var(--mono);font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);display:block;margin-bottom:4px}
  .evidence .brand{display:inline-block;background:var(--surface);border:1px solid var(--hair-2);border-radius:4px;padding:1px 6px;margin:2px 4px 0 0;font-size:11px;color:var(--ink)}
  .evidence .ask{font-family:var(--mono);color:var(--accent-ink)}
  .supply-banner{margin-top:22px;padding:14px 16px;border-radius:11px;background:var(--surface-2);border:1px solid var(--hair);border-left:3px solid var(--mix);font-size:14px;color:var(--ink-2);max-width:72ch}
  .supply-banner b{color:var(--ink)}
  .conflict{font-size:12px;line-height:1.5;color:var(--mix-ink);background:color-mix(in srgb,var(--mix) 12%,transparent);border:1px solid color-mix(in srgb,var(--mix) 35%,transparent);border-radius:8px;padding:7px 10px;font-weight:600}
  .evidence .amz{display:block;margin-top:7px;padding-top:7px;border-top:1px dashed var(--hair-2)}
  .evidence .amz a{color:var(--ink-2)} .evidence .amz b{color:var(--ink);font-family:var(--mono)}
  .quote{font-size:13.5px;color:var(--ink-2);border-left:2px solid var(--hair-2);padding:2px 0 2px 12px;font-style:italic}
  .quote a{color:var(--muted);font-style:normal;font-family:var(--mono);font-size:11px;text-decoration:none;display:inline-block;margin-top:5px}
  .quote a:hover{color:var(--accent)}
  .pain-list{display:flex;flex-direction:column;gap:12px}
  .niche-head{margin:30px 0 4px;font-size:18px;font-weight:750;letter-spacing:-.01em}
  .niche-sub{color:var(--muted);font-size:13px;margin:0 0 14px;max-width:72ch}
  .niche-list{display:flex;flex-direction:column;gap:10px}
  .niche{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 16px;background:var(--surface);border:1px solid var(--hair);border-left:3px solid var(--mix);border-radius:12px;padding:14px 16px}
  .niche h4{margin:0;font-size:15px;font-weight:700} .niche h4 small{font-family:var(--mono);font-size:11px;color:var(--muted);font-weight:500;margin-left:6px}
  .niche .cnt{font-family:var(--mono);font-size:12px;color:var(--mix-ink);font-weight:600;white-space:nowrap}
  .niche .quote{grid-column:1/-1}
  .pain{display:grid;grid-template-columns:auto 1fr auto;gap:16px;align-items:center;background:var(--surface);border:1px solid var(--hair);border-left:3px solid var(--neg);border-radius:12px;padding:16px 18px;box-shadow:var(--shadow)}
  .pain .idx{font-family:var(--mono);font-size:13px;color:var(--neg-ink);font-weight:700}
  .pain .txt{font-size:15px;color:var(--ink)} .pain .txt b{color:var(--neg-ink);font-weight:700;background:color-mix(in srgb,var(--neg) 12%,transparent);padding:0 2px;border-radius:3px}
  .pain .src{font-family:var(--mono);font-size:11.5px;color:var(--muted);text-align:right;white-space:nowrap;display:flex;flex-direction:column;gap:3px;align-items:flex-end}
  .pain .src .up{color:var(--accent)}
  .pain .src .permalink{color:var(--ink-2);text-decoration:none;font-weight:600;border:1px solid var(--hair-2);border-radius:20px;padding:4px 11px;margin-top:3px;transition:background .12s,color .12s,border-color .12s}
  .pain .src .permalink:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
  footer{padding:34px 0 60px} .method{font-size:12.5px;color:var(--muted);max-width:72ch;line-height:1.7} .method b{color:var(--ink-2)}
  .disclaimer{margin-top:16px;font-family:var(--mono);font-size:11px;color:var(--muted)}
  #tip{position:fixed;pointer-events:none;z-index:50;opacity:0;transition:opacity .1s;background:var(--ink);color:var(--plane);font-family:var(--mono);font-size:11.5px;padding:9px 11px;border-radius:8px;line-height:1.5;box-shadow:0 6px 20px rgba(0,0,0,.3);max-width:220px}
  #tip .tt{font-weight:700;font-size:12.5px;margin-bottom:4px} #tip .tr{display:flex;justify-content:space-between;gap:14px}
  .bubble{cursor:default;transition:opacity .12s} .quad-figure:hover .bubble{opacity:.45} .quad-figure .bubble:hover{opacity:1}
  @media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
  @media (max-width:720px){.two-col,.cards,.takeaways{grid-template-columns:1fr}.meta div{flex-basis:50%}.pain{grid-template-columns:auto 1fr}.pain .src{grid-column:2;text-align:left;align-items:flex-start}}
</style>
<div id="tip"></div>
<div class="wrap">
  <header class="mast">
    <div class="mast-top">
      <div class="brand">REDDIT · <b>选品情报</b> // SIGNAL REPORT</div>
    </div>
    <h1 class="title">从社区噪音里<br>挖出 <span class="kw" id="kw"></span> 的机会</h1>
    <p class="subtitle">按热度、口碑、机会度(低饱和)定位细分方向。</p>
    <div class="meta" id="meta"></div>
  </header>
  <section><div class="sec-head"><span class="sec-num">01</span><div><h2>核心结论</h2><p class="sec-sub">模型对全部讨论的提炼</p></div></div>
    <p class="summary" id="summary"></p><div class="supply-banner" id="lowBanner" hidden></div><div class="supply-banner" id="supplyBanner" hidden></div><div class="takeaways" id="takeaways"></div></section>
  <section><div class="sec-head"><span class="sec-num">02</span><div><h2>机会象限图</h2><p class="sec-sub">X 轴=需求热度　Y 轴=机会度(越靠上＝竞争越不饱和)　气泡大小=提及量　颜色=情感</p></div></div>
    <div class="quad-legend">
      <span class="legend-item"><span class="dot" style="background:var(--pos)"></span>正面为主</span>
      <span class="legend-item"><span class="dot" style="background:var(--mix)"></span>褒贬混合</span>
      <span class="legend-item"><span class="dot" style="background:var(--neg)"></span>负面为主 <span style="color:var(--accent-ink);font-weight:600">= 潜在机会</span></span>
    </div>
    <div class="quad-figure" id="quadFig"></div>
    <div class="quad-note" id="callouts"></div></section>
  <section><div class="sec-head"><span class="sec-num">03</span><div><h2>高频词与情感概览</h2><p class="sec-sub">左：词云按真实词频（大家在讨论什么）　右：各方向真实口碑分布（好坏）</p></div></div>
    <div class="two-col">
      <div class="panel"><p class="eyebrow">Keyword Cloud（真实词频）</p><div class="cloud" id="cloud"></div>
        <div class="cloud-key"><span><i style="background:var(--brand-ink)"></i>品牌</span><span><i style="background:var(--neg-ink)"></i>痛点词</span><span><i style="background:var(--ink-2)"></i>材质/功能/场景</span><span style="color:var(--muted)">字号＝出现频率</span></div></div>
      <div class="panel"><p class="eyebrow">Sentiment / 各方向口碑</p><div id="sentList"></div>
        <p style="font-size:11.5px;color:var(--muted);margin:6px 0 0;font-family:var(--mono)">■正面 ■混合 ■负面　右为净情感分</p></div>
    </div></section>
  <section><div class="sec-head"><span class="sec-num">04</span><div><h2>细分方向详情</h2><p class="sec-sub">按机会分排序，含真实提及量/热度/饱和度与一条代表原话(可点回 Reddit)</p></div></div>
    <div class="cards" id="cards"></div>
    <div id="nicheBox" hidden><h3 class="niche-head">小众需求</h3><p class="niche-sub" id="nicheSub"></p><div class="niche-list" id="niche"></div></div></section>
  <section><div class="sec-head"><span class="sec-num">05</span><div><h2>痛点信号流</h2><p class="sec-sub">真实高赞抱怨原话——蓝海最直接的线索</p></div></div>
    <div class="pain-list" id="painList"></div></section>
  <footer><p class="eyebrow" style="margin-bottom:10px">方法论</p>
    <p class="method"><b>数据链路：</b>关键词 → 目标社区 → 抓取 Reddit 帖子+评论(全文命中痛点) → 去噪 → 聚成细分方向 → 定量算热度/新鲜度(真时间戳)、情感(LLM 逐条)、饱和度 → 可视化。<br>
    <b>指标口径：</b>提及量/热度/新鲜度=从真实数据定量计算;情感=LLM 逐条标注后汇总;饱和度=Reddit 判读(品牌点名、求推荐是否被满足、负面率；评分标尺+校准锚点，3 次取中位)占 30%，Amazon 实测供给分(搜索第 1 页前 10 个自然位中，由 LLM 按标题判定的「专门产品」个数、其评论数中位、其中千评以上个数；不用 Amazon 结果总数——它统计的是沾边商品且有上限)占 70%；先算出本品类两者的平均差（偏差大时单独作为品类结论展示），只有某方向的差距偏离这个平均值 ≥25 分时，才标为「供需信号矛盾」。未接 Amazon 时仅用 Reddit 判读。<br>
    <b>新鲜度说明：</b>Reddit 数据样本偏近期(近 90 天占比高),故本轮新鲜度区分度弱;接入更长历史后可增强。</p>
    <p class="disclaimer" id="disc"></p></footer>
</div>
<script>
const REPORT = __DATA__;
const sentColor={pos:'var(--pos)',mix:'var(--mix)',neg:'var(--neg)',thin:'var(--mix)'},sentGlyph={pos:'▲',mix:'◆',neg:'▼',thin:'·'},sentLabel={pos:'正面',mix:'混合',neg:'负面',thin:'样本少'};
const zoneMap={op:{cls:'z-op',label:'机会区'},red:{cls:'z-red',label:'红海'},blue:{cls:'z-blue',label:'蓝海苗头'},tail:{cls:'z-tail',label:'长尾'},thin:{cls:'z-tail',label:'样本少'}};

// 小众需求: directions with too few mentions get no zone, card or bubble, only a line at the end
// of section 04. A low-sample report keeps them all, since its banner already says no verdict.
const NB=REPORT.niche_below||0,ALL=REPORT.directions,isNiche=d=>!REPORT.low_sample&&d.mentions<NB;
const NICHE=ALL.filter(isNiche).sort((a,b)=>b.mentions-a.mentions);REPORT.directions=ALL.filter(d=>!isNiche(d));
// header
document.getElementById('kw').textContent=REPORT.keyword;
document.getElementById('summary').innerHTML=REPORT.summary_html;
if(REPORT.low_sample){const b=document.getElementById('lowBanner');b.innerHTML='<b>样本不足，不作选品判断：</b>进入分析的讨论不到 100 条，各方向的区域、热度和情绪都不稳定。Reddit 上这个品类讨论太少，建议换更常用的关键词，或用其他渠道验证。';b.hidden=false;}
if(REPORT.supply_banner){const b=document.getElementById('supplyBanner');b.innerHTML='<b>Amazon 供给侧整体判断：</b>'+REPORT.supply_banner;b.hidden=false;}
document.getElementById('disc').textContent='⚠ 本报告为真实抓取数据('+REPORT.meta.posts+' 帖 + '+REPORT.meta.comments+' 评论，去噪后 '+REPORT.meta.analyzed+' 条)。情感/饱和度含近似判读。'+(REPORT.quick?' 快速模式：社区和查询词较少，样本偏小，结论仅供初筛。':'');
const m=REPORT.meta;
document.getElementById('meta').innerHTML=`
 <div><div class="k">关键词</div><div class="v">${REPORT.keyword}</div></div>
 <div><div class="k">覆盖社区</div><div class="v">${m.subreddits} <small>subreddit</small></div></div>
 <div><div class="k">分析讨论</div><div class="v">${m.analyzed.toLocaleString()} <small>去噪后</small></div></div>
 <div><div class="k">细分方向</div><div class="v">${REPORT.directions.length} <small>个${NICHE.length?' + 小众 '+NICHE.length:''}</small></div></div>
 <div><div class="k">时间窗</div><div class="v" style="font-size:14px">${REPORT.window}</div></div>`;
document.getElementById('takeaways').innerHTML=REPORT.takeaways.map(t=>`
 <div class="takeaway ${t.cls}"><div class="tag">${t.tag}</div><div class="big">${t.big}</div><p>${t.p}</p></div>`).join('');

// quadrant  (Y = 机会度 = 100 - sat)
(function(){const D=REPORT.directions,W=820,H=560,mL=58,mR=42,mT=40,mB=58,pW=W-mL-mR,pH=H-mT-mB;
 const x=v=>mL+(v/100)*pW,y=v=>mT+(1-v/100)*pH,r=m=>12+Math.sqrt(m/Math.max(...D.map(d=>d.mentions)))*30;
 const midX=x(50),midY=y(50);let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="机会象限图">`;
 s+=`<rect x="${midX}" y="${mT}" width="${mL+pW-midX}" height="${midY-mT}" fill="var(--accent)" opacity="0.05"/>`;
 for(let v=0;v<=100;v+=20){s+=`<line x1="${x(v)}" y1="${mT}" x2="${x(v)}" y2="${mT+pH}" stroke="var(--grid)"/>`;s+=`<line x1="${mL}" y1="${y(v)}" x2="${mL+pW}" y2="${y(v)}" stroke="var(--grid)"/>`;}
 s+=`<line x1="${midX}" y1="${mT}" x2="${midX}" y2="${mT+pH}" stroke="var(--hair-2)" stroke-width="1.5" stroke-dasharray="2 4"/>`;
 s+=`<line x1="${mL}" y1="${midY}" x2="${mL+pW}" y2="${midY}" stroke="var(--hair-2)" stroke-width="1.5" stroke-dasharray="2 4"/>`;
 s+=`<line x1="${mL}" y1="${mT+pH}" x2="${mL+pW}" y2="${mT+pH}" stroke="var(--hair-2)" stroke-width="1.5"/><line x1="${mL}" y1="${mT}" x2="${mL}" y2="${mT+pH}" stroke="var(--hair-2)" stroke-width="1.5"/>`;
 const T=(tx,ty,t,c,anc)=>`<text x="${tx}" y="${ty}" ${anc?'text-anchor="'+anc+'"':''} fill="${c}" font-family="var(--mono)" font-size="12" font-weight="600" opacity="0.8">${t}</text>`;
 s+=T(mL+pW-8,mT+18,'↗ 机会区 · 高需求 × 低饱和','var(--accent-ink)','end');
 s+=T(mL+8,mT+18,'🌱 蓝海苗头 · 低需求 × 低饱和','var(--muted)');
 s+=T(mL+8,mT+pH-10,'长尾 · 低需求 × 高饱和','var(--muted)');
 s+=T(mL+pW-8,mT+pH-10,'🌊 红海 · 高需求 × 高饱和','var(--muted)','end');
 s+=`<text x="${mL+pW/2}" y="${H-14}" text-anchor="middle" fill="var(--ink-2)" font-family="var(--mono)" font-size="12" font-weight="600">需求热度 →</text>`;
 s+=`<text x="16" y="${mT+pH/2}" text-anchor="middle" fill="var(--ink-2)" font-family="var(--mono)" font-size="12" font-weight="600" transform="rotate(-90 16 ${mT+pH/2})">机会度 · 低饱和 →</text>`;
 D.forEach(d=>{const cx=x(d.heat),cy=y(100-d.sat),rr=r(d.mentions),col=sentColor[d.sent];
  s+=`<g class="bubble" data-id="${d.id}"><circle cx="${cx}" cy="${cy}" r="${rr}" fill="${col}" fill-opacity="0.16" stroke="${col}" stroke-width="2"/><circle cx="${cx}" cy="${cy}" r="3.5" fill="${col}"/>`;
  s+=`<text x="${cx}" y="${cy-rr-6}" text-anchor="middle" fill="var(--ink)" font-family="var(--sans)" font-size="12" font-weight="700">${sentGlyph[d.sent]} ${d.cn}</text></g>`;});
 s+=`</svg>`;document.getElementById('quadFig').innerHTML=s;
 const tip=document.getElementById('tip');
 document.querySelectorAll('.bubble').forEach(g=>{const d=D.find(x=>x.id===g.dataset.id);
  g.addEventListener('mousemove',e=>{tip.innerHTML=`<div class="tt">${d.cn}</div>`+
   `<div class="tr"><span>需求热度</span><span>${d.heat}</span></div><div class="tr"><span>饱和度</span><span>${d.sat}</span></div>`+
   `<div class="tr"><span>机会度</span><span>${100-d.sat}</span></div><div class="tr"><span>提及量</span><span>${d.mentions}</span></div>`+
   `<div class="tr"><span>净情感</span><span>${d.sent==='thin'?'样本少':(d.score>0?'+':'')+d.score+' · '+sentLabel[d.sent]}</span></div>`;
   tip.style.opacity=1;let tx=e.clientX+14;if(tx+230>innerWidth)tx=e.clientX-230;tip.style.left=tx+'px';tip.style.top=(e.clientY+14)+'px';});
  g.addEventListener('mouseleave',()=>tip.style.opacity=0);});
})();
// callouts: top opportunity + top red-ocean
(function(){const D=REPORT.directions,op=D.filter(d=>d.zone==='op').sort((a,b)=>b.heat-a.heat)[0],
  red=D.filter(d=>d.zone==='red').sort((a,b)=>b.heat-a.heat)[0];let h='';
 if(op)h+=`<div class="callout"><span class="mark">↗ 机会区</span><p><b>${op.cn}</b>落在「高需求 × 低饱和」：提及 ${op.mentions}、热度 ${op.heat}，需求不低而供给还没填满。</p></div>`;
 if(red)h+=`<div class="callout"><span class="mark">🌊 红海/成熟</span><p><b>${red.cn}</b>需求也高但饱和度 ${red.sat}：已有共识答案，切入价值有限。</p></div>`;
 document.getElementById('callouts').innerHTML=h;})();
// cloud (elliptical spiral)
(function(){const el=document.getElementById('cloud');function ell(x,y,rx,ry){return (x*x)/(rx*rx)+(y*y)/(ry*ry)<=1;}
 function ov(a,b){return !(a.l+a.w<b.l||b.l+b.w<a.l||a.t+a.h<b.t||b.t+b.h<a.t);}
 function layout(){el.innerHTML='';const W=el.clientWidth,H=el.clientHeight,cx=W/2,cy=H/2,rx=W/2-4,ry=H/2-4,placed=[];
  [...REPORT.cloud].sort((a,b)=>b.s-a.s).forEach(w=>{const sp=document.createElement('span');sp.className='s-'+w.c;sp.textContent=w.t;sp.style.fontSize=w.s+'px';sp.style.visibility='hidden';el.appendChild(sp);
   const bw=sp.offsetWidth+10,bh=sp.offsetHeight+4;let ang=0,rad=0,ok=false;
   for(let k=0;k<2400;k++){const px=Math.cos(ang)*rad*(rx/ry),py=Math.sin(ang)*rad,box={l:px-bw/2,t:py-bh/2,w:bw,h:bh};
    if(ell(box.l,box.t,rx,ry)&&ell(box.l+bw,box.t,rx,ry)&&ell(box.l,box.t+bh,rx,ry)&&ell(box.l+bw,box.t+bh,rx,ry)&&!placed.some(p=>ov(p,box))){
     sp.style.left=(cx+px)+'px';sp.style.top=(cy+py)+'px';sp.style.visibility='visible';placed.push(box);ok=true;break;}ang+=0.35;rad+=0.4;}
   if(!ok)sp.remove();});}
 layout();let t;addEventListener('resize',()=>{clearTimeout(t);t=setTimeout(layout,150);});})();
// sentiment list
(function(){const top=[...REPORT.directions].sort((a,b)=>b.mentions-a.mentions).slice(0,7);
 document.getElementById('sentList').innerHTML=top.map(d=>{const[p,mm,n]=d.split;
  if(d.sent==='thin')return `<div class="sent-row"><span class="name">${d.cn}</span><span class="sent-bar"></span><span class="score" style="color:var(--mix-ink);width:auto">样本少</span></div>`;
  return `<div class="sent-row"><span class="name">${d.cn}</span><span class="sent-bar" title="正${p}% 混${mm}% 负${n}%"><i class="p" style="width:${p}%"></i><i class="m" style="width:${mm}%"></i><i class="n" style="width:${n}%"></i></span><span class="score" style="color:${d.score>0?'var(--pos-ink)':'var(--neg-ink)'}">${d.score>0?'+':''}${d.score}</span></div>`;}).join('')
  +(NICHE.length?`<p style="font-size:11.5px;color:var(--muted);margin:2px 0 12px">小众需求：${NICHE.map(d=>`${d.cn} ${d.mentions} 条`).join('、')}（讨论太少，不显示比例，见第 04 节）</p>`:'');})();
// cards
(function(){const order={op:0,blue:1,red:2,tail:3,thin:4};
 const sorted=[...REPORT.directions].sort((a,b)=>(order[a.zone]-order[b.zone])||(b.heat-a.heat));
 document.getElementById('cards').innerHTML=sorted.map((d,i)=>{const z=zoneMap[d.zone];
  return `<div class="card"><div class="card-top"><div><div class="card-rank">#${String(i+1).padStart(2,'0')}</div><h3>${d.cn}</h3><div class="en">${d.en}</div></div><span class="zone-badge ${z.cls}">${z.label}</span></div>
   <div class="metrics"><div class="metric"><div class="ml">提及量</div><div class="mv">${d.mentions}</div></div><div class="metric"><div class="ml">需求热度</div><div class="mv">${d.heat}</div></div><div class="metric"><div class="ml">饱和度</div><div class="mv">${d.sat}</div></div>
    <div class="metric" style="flex:1;min-width:0;align-items:flex-end;text-align:right"><div class="ml">口碑</div><div class="senttag" style="color:${d.score>0?'var(--pos-ink)':(d.sent==='mix'?'var(--mix-ink)':'var(--neg-ink)')}"><i style="background:${sentColor[d.sent]}"></i>${sentGlyph[d.sent]} ${sentLabel[d.sent]}${d.sent==='thin'?'':' '+(d.score>0?'+':'')+d.score}</div></div></div>
   <div class="oppbar-wrap"><div class="oppbar"><i style="width:${100-d.sat}%"></i></div><span class="opplabel">机会度 <b>${100-d.sat}</b></span></div>
   ${d.conflict?`<div class="conflict">⇄ ${d.conflict_text}</div>`:''}
   ${d.note?`<div class="card-note">⚠ ${d.note}</div>`:''}
   ${(d.evidence||(d.brands&&d.brands.length)||d.asks)?`<div class="evidence"><span class="el">饱和度依据（可核实）</span>
     ${d.evidence?`${d.evidence}<br>`:''}
     ${(d.brands&&d.brands.length)?`点名品牌：${d.brands.map(b=>`<span class="brand">${b}</span>`).join('')}　`:'点名品牌：<span class="brand">无</span>　'}
     <span class="ask">求推荐帖 ~${d.asks||0} 条</span>
     ${d.amazon?`<span class="amz">Amazon 第 1 页（搜 <a href="${d.amazon.url}" target="_blank" rel="noopener">「${d.amazon.query}」↗</a>）：前 ${d.amazon.n} 名中专门产品 <b>${d.amazon.dedicated}</b> 个，其余 <b>${d.amazon.generic}</b> 个为通用款${d.amazon.dedicated?` · 专门款评论中位 <b>${d.amazon.ded_median_reviews.toLocaleString()}</b> · 千评以上 <b>${d.amazon.ded_strong}</b> 个${d.amazon.ded_median_price?` · 中位价 <b>$${d.amazon.ded_median_price}</b>`:''}`:' · 暂无专门产品'}${(d.amazon.proofs&&d.amazon.proofs.length)?`<br>判定依据（标题原话）：${d.amazon.proofs.map(p=>`<span class="brand" title="${p.title.replace(/"/g,'&quot;')}">${p.quote}</span>`).join('')}`:''}<br>Amazon 供给分 <b>${d.amazon.score}</b> × ${Math.round(d.amazon.w*100)}% + Reddit 判读 <b>${d.sat_reddit}</b> × ${100-Math.round(d.amazon.w*100)}% = 饱和度 <b>${d.sat}</b></span>`:''}</div>`:''}
   <div class="quote">${d.quote}<br><a href="${d.url}" target="_blank" rel="noopener">↳ ${d.sub} · 查看原帖 ↗</a></div></div>`;}).join('');})();
// niche
(function(){if(!NICHE.length)return;document.getElementById('nicheBox').hidden=false;
 document.getElementById('nicheSub').textContent=`讨论不足 ${NB} 条，热度和口碑比例都不可靠，不放进象限图、也不分区。可以当作线索：值得的话，单独换关键词再查。`;
 document.getElementById('niche').innerHTML=NICHE.map(d=>`<div class="niche"><h4>${d.cn}<small>${d.en}</small></h4><span class="cnt">提及 ${d.mentions} 条</span>
   <div class="quote">${d.quote}${d.url&&d.url!=='#'?`<br><a href="${d.url}" target="_blank" rel="noopener">↳ ${d.sub} · 查看原帖 ↗</a>`:''}</div></div>`).join('');})();
// painpoints
(function(){document.getElementById('painList').innerHTML=REPORT.painpoints.map((p,i)=>
  `<div class="pain"><span class="idx">S${String(i+1).padStart(2,'0')}</span><span class="txt">"${p.t}"</span>
   <span class="src"><span>${p.sub} · <span class="up">▲ ${p.up}</span></span><a class="permalink" href="${p.url}" target="_blank" rel="noopener">↗ 查看原帖</a></span></div>`).join('');})();
</script>"""

html = TEMPLATE.replace("__KW__", REPORT["keyword"]).replace("__DATA__", json.dumps(REPORT, ensure_ascii=False))
OUT.write_text(html, encoding="utf-8")
print(f"✅ 报告已生成: {OUT}  ({len(html):,} bytes)")
