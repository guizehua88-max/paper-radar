"""Transparent, evidence-limited relevance screening. No DOI oracle or AI API.

Input is only the title and supplied abstract/context. Labels are hypotheses for
reading triage, not claims that the full paper has been read or validated.
"""
from __future__ import annotations
import hashlib
import html
import re
import unicodedata
from html.parser import HTMLParser

RULE_VERSION = "2026-09-30.12"
TIERS = {"direct": "直接相关候选", "cross": "交叉相关候选", "transferable": "可迁移方法候选",
         "background": "邻近与背景", "uncertain": "待核路线", "exclude": "低相关/排除"}
PROCESSES = {
    "gas_n2_h2_ammonia_synthesis": "N₂/H₂气相合成氨",
    "ammonia_route_unspecified": "制氨路线未明确",
    "ammonia_decomposition": "氨分解制氢", "ammonia_borane_hydrolysis": "氨硼烷水解",
    "electrochemical_n2_reduction": "电化学氮还原", "electrochemical_nitrate_reduction": "电化学硝酸盐还原",
    "electrochemical_nitrite_reduction": "电化学亚硝酸盐还原",
    "chemical_looping_ammonia_synthesis": "化学链/晶格氮循环制氨",
    "photocatalytic_n2_reduction": "光催化固氮", "plasma_ammonia_synthesis": "等离子体制氨",
    "photocatalytic_nitrate_reduction": "光催化硝酸盐转化", "mechanochemical_ammonia": "机械化学制氨",
    "methane_dry_reforming": "甲烷干重整", "reverse_water_gas_shift": "逆水煤气变换",
    "hydrogen_evolution": "析氢反应", "materials_synthesis_only": "材料制备/结构调控",
    "general_catalysis": "通用催化方法", "other_catalytic_reaction": "其他催化反应",
    "biological_nitrogen_fixation": "生物固氮（非气相热催化）",
    "ammonia_supply_chain_context": "氨相关分离/检测/资源系统（非制氨反应证据）",
    "no_target_chemical_process": "未见目标科研过程", "unknown": "过程待核实",
}
HEATING = {"not_stated": "未交代供热方式", "reaction_joule": "反应阶段焦耳/电阻供热",
           "preparation_joule": "焦耳热用于制备", "reaction_programmable": "反应温度程序/淬冷",
           "electrochemical_not_joule": "电化学驱动，不等于床层焦耳热",
           "electric_field_not_joule": "电场辅助，未证实焦耳供热",
           "light_driven": "光驱动", "plasma_driven": "等离子体驱动", "mechanical_driven": "机械力驱动", "none_relevant": "无相关供热证据"}
USES = {"catalyst_preparation": "材料与催化剂制备", "reaction_operation": "反应运行",
        "kinetics_model": "理论/机理/动力学建模", "reactor_design": "反应器/流程设计",
        "measurement_validation": "测量与证据校验", "review_background": "综述与背景学习",
        "research_optimization": "实验优化", "unrelated": "未见明确用途", "unclear": "用途待定"}

class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"): self.hidden += 1
        elif tag in ("br", "p", "div", "li"): self.parts.append(" ")
    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden: self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

def plain(value):
    p = _PlainText()
    try: p.feed(str(value or "")); return re.sub(r"\s+", " ", html.unescape("".join(p.parts))).strip()
    except Exception: return str(value or "")[:30000]

def norm(value):
    return unicodedata.normalize("NFKC", plain(value)).lower().replace("–", "-").replace("—", "-")

def has(pattern, value): return bool(re.search(pattern, value, re.I))

def carbon_material(value):
    """Require material evidence, not the element word in energy/emissions prose.

    Remove only local climate phrases in this topic-only view. Never veto the
    whole record: a low-carbon process can still use a real carbon support.
    Other scientific route/heating decisions continue to use the original text.
    """
    material_text = re.sub(
        r"\b(?:carbon[- ](?:free|neutral)|(?:net[- ]zero|zero|low)[- ]carbon)\b"
        r"|(?:净零|低|零|无)碳|碳(?:排放|足迹|中和|减排|交易|税)",
        " ", value, flags=re.I)
    return has(
        r"\b(?:graphene|graphite)\b"
        r"|\bcarbon[- ](?:nano(?:tube|fib(?:er|re)|sheet|sphere|dot)s?|supports?|supported|carriers?|foams?|papers?|cloth|felt|black|powders?|monoliths?|matri(?:x|ces)|frameworks?|materials?|substrates?|electrodes?|overcoats?|coatings?|shells?|layers?)\b"
        r"|\bcarbon[- ](?:based|derived)[- ](?:catalysts?|supports?|electrodes?)\b"
        r"|\b(?:activated|porous|mesoporous|microporous|graphitic|amorphous|glassy|hard|soft|doped|conductive|conducting|waste)[- ]carbon\b"
        r"|\b(?:supported|loaded|dispersed|deposited|anchored|grown|immobilized)[- ]on[- ]carbon\b"
        r"|\b(?:ru|fe|co|ni|cu|pt|pd)\s*/\s*c(?:arbon)?\b"
        r"|石墨烯|石墨(?:载体|材料|泡沫|支撑)"
        r"|(?:活性|多孔|介孔|微孔|导电|掺氮|氮掺杂|石墨化|无定形|硬|软|玻璃态)碳"
        r"|碳(?:纳米管|纳米纤维|纳米片|载体|支撑|材料|泡沫|纸|布|毡|黑|粉|壳|包覆|涂层|膜|骨架|电极|基催化剂|基载体)"
        r"|(?:负载|沉积|锚定)于碳", material_text)


def positive_context(value):
    # Explicit negative control clauses are not positive evidence of a method.
    clauses = re.split(r"[。；;,]|(?<!\d)\.(?!\d)", value)
    return " ; ".join(c for c in clauses if not has(r"(?:没有|未(?:用|说明|提供|交代|涉及|读|测试)|不(?:涉及|是|指|代表)|非n2|不把)|\b(?:unlike|instead of|in contrast to|not (?:an? |via |using )?(?:electrochemical|photocatalytic|joule))\b|\b(?:was|were|is|are) not (?:used|studied|employed)|\b(?:compare|introduction|previous studies)\b", c))


def source_content_notes(title, abstract, supporting_data=False):
    """Narrow, explainable form/quality hints; never rewrite source type/basis.

    Inspect the supplied source text, not positive_context's filtered clauses.
    Absence of a hint is NOT evidence that text is complete or scientifically
    valid. Two independent defect classes are required for a damage warning.
    """
    a = norm(abstract)
    opening = re.split(r"[.!?]", a, maxsplit=1)[0]
    public_challenge = (
        has(r"^(?:abstract\s+)?(?:a\s+)?public challenge to\b", a)
        and not has(r"\b(?:was|were|previously|earlier)\b", opening)
        and has(r"challenge specifications|publicly challenges|(?:submission|challenge) deadline|submit.{0,100}deadline", a)
    )
    kind, label, note = "unspecified", "内容类型待核", ""
    if supporting_data:
        kind, label = "supporting_dataset", "配套数据（规则识别）"
        note = "来源文本说明这是复现数据/输入文件等配套资料，非独立论文；保留上游原始文种供核对。"
    elif public_challenge:
        kind, label = "public_challenge", "公开挑战公告（规则识别）"
        note = "来源文本以公开挑战与参与要求为主；其中声明不等于已验证的研究结果，保留上游原始文种供核对。"

    # Deliberately not a general typo/grammar detector. These local patterns
    # require explicit identifier placeholders AND empty numerical quantities;
    # lack of a final full stop, short text or a lone bracket never suffices.
    patterns = (
        ("identifier_placeholder", "标识符疑似占位/缺失",
         r"\bORCID\s*:\s*[-–—]{2,}(?=\s|[;,.]|$)|\bDOI\s*:?\s*\.+/[^\s;,)]+"),
        ("missing_numeric_value", "数量单位前缺少数值",
         r"(?:<=|>=|[<>≤≥])\s*[-−.]?\s*(?:%|(?:kcal|kj)\s*/\s*mol\b)"
         r"|\btotal atoms\s*:\s*[,.;-]\s*atoms\b"),
    )
    signals = []
    for signal_kind, signal_label, pattern in patterns:
        match = re.search(pattern, abstract, re.I)
        if match:
            signals.append({"kind": signal_kind, "label": signal_label,
                            "excerpt": match.group(0)[:120]})
    quality_notes = []
    if len(signals) >= 2:
        quality_notes.append({
            "code": "suspected_incomplete_source_text",
            "message": "来源文本疑似残缺：同时出现标识符占位和数值空缺；请核对原始页面/全文，不应据此引用精确数值。这不改变来源字段的证据级别。",
            "signals": signals,
        })
    return {"content_kind": kind, "content_kind_label": label,
            "content_kind_note": note, "quality_notes": quality_notes}


def primary_stoichiometric_bound(title, abstract):
    """Require present-work analytical bounds, not background or mixed experiments.

    Use the unfiltered supplied abstract: positive_context intentionally drops
    comparison clauses, but a bound study may itself compare conditioning options.
    A measured input composition is not a measured reaction-product yield.
    """
    if not abstract:
        return False
    raw = norm(abstract)
    marker = re.search(r"\b(?:this (?:study|work)|the present (?:study|work)|herein|here we|we (?:derive|quantify|calculate|formulate|establish))\b", raw)
    if not marker:
        return False
    focus = raw[marker.start():]
    sentences = [s.strip() for s in re.split(r"(?<!\d)\.(?!\d)|[!?。]", focus) if s.strip()]
    # The main method must occur near the explicit current-work statement.
    # Later background/auxiliary calculations cannot supply primary evidence.
    primary = " ".join(s for s in sentences[:2] if not has(
        r"\b(?:previous|prior|earlier|literature|introduction|background|supporting|auxiliary)\b"
        r"|\b(?:does|do|did) not.{0,30}(?:derive|compute|calculate|use)", s))
    if not (has(r"\bstoichiometric (?:upper )?(?:bounds?|limits?|ceilings?)\b", primary)
            and has(r"\banalytical\b|\blinear programming\b", primary)
            and has(r"\b(?:quantif\w*|deriv\w*|calculat\w*|formulat\w*|establish\w*)\b", primary)):
        return False
    if has(r"\b(?:preparation|fabrication|synthesis) of .{0,50}(?:catalysts?|nanoparticles?|coatings?)\b", norm(title)):
        return False
    for sentence in sentences:
        if has(r"\b(?:previous|prior|earlier|literature|background)\b", sentence):
            continue
        negated_measurement = has(r"\b(?:not|rather than).{0,35}(?:measured|experimental)\b", sentence)
        measured_output = has(
            r"\b(?:measured|experimental)\b.{0,45}\b(?:yields?|conversion|rates?|performance)\b"
            r"|\b(?:we|this study|this work)\b.{0,35}\bmeasur\w*\b.{0,35}\b(?:yields?|conversion|rates?|performance)\b",
            sentence)
        current_preparation = has(r"\b(?:we|this study|this work)\b.{0,35}\b(?:prepare|synthesize|fabricate)\w*\b.{0,45}\b(?:catalysts?|nanoparticles?|coatings?)\b", sentence)
        if (measured_output and not negated_measurement) or current_preparation:
            return False
    return True


def current_method_sentences(abstract):
    """Local current-work evidence; background/negation cannot supply a method."""
    return [s for s in re.split(r"(?<!\d)\.(?!\d)|[!?。]", norm(abstract)) if not has(
        r"\b(?:previous|prior|earlier|background|unlike)\b"
        r"|\b(?:does|do|did|was|were|is|are) not\b|\bnot (?:used|studied|employed)\b", s)]


def primary_measurement_method(title, abstract):
    """Recognize a measurement-method subject, not an incidental assay/model fit."""
    t = norm(title)
    if has(r"\b(?:simulation|simulated|in.silico|computational|numerical model)\b", t):
        return False
    instrument = r"gas[- ]selective electrode|ion[- ]selective electrode|potentiometr|calorimet(?:er|ry|ric)|chromatograph|spectrophotometr"
    method = r"quantifi(?:cation|cation method)|quantitation|calorimetry|measurement (?:method|accuracy)"
    active = current_method_sentences(abstract)
    if has(method, t) and has(instrument, t + " ; " + " ; ".join(active)):
        return True
    # A separate supporting measurement paragraph must not override a catalyst
    # experiment: require an explicit present-work evaluation/development method.
    return any(has(r"\b(?:we|this (?:work|study|paper)|primary objective).{0,70}(?:evaluat|develop|assess|validat|propos)", s)
               and has(instrument, s) and has(method, s) for s in active)


def catalytic_data_method(title):
    """Title identifies both catalytic materials and a data-method contribution."""
    t = norm(title)
    return (has(r"catalys|catalytic", t)
            and has(r"machine[- ]learning|data[- ]driven|neural network|deep[- ]learning", t)
            and has(r"screen|design|reliab|validat|predict|uncertainty|extrapolat", t)
            and not has(r"clinical|medical|patient|disease|diagnos|pharmac|enzyme|protein|antibiotic|therapeutic", t))


def catalytic_ml_experiment_optimization(abstract):
    """Explicit present-work model-guided experiments, not a model benchmark.

    This runs only for a catalytic data-method title. Keep evidence local: a
    dataset of measurements, supporting synthesis or proposed future validation
    does not establish an experimentally exercised optimisation workflow.
    """
    for sentence in current_method_sentences(abstract):
        if has(r"\b(?:future|proposed|planned|will|would|could|might|published dataset)\b|\b(?:in|from) (?:the )?literature\b", sentence):
            continue
        if has(r"\bnot (?:an? )?experimentally validated\b|\bno (?:new )?catalysts?\b.{0,50}\bexperimentally (?:tested|evaluated)\b", sentence):
            continue
        closed_loop = (
            has(r"closed[- ]loop.{0,100}(?:machine[- ]learning|prediction).{0,50}experiment", sentence)
            and has(r"\bcatalysts?\b.{0,50}\b(?:are|were|was|is) experimentally (?:tested|evaluated)\b", sentence))
        guided_synthesis = (
            has(r"\bguided by (?:this|the|our) (?:framework|model|prediction|workflow)\b", sentence)
            and has(r"\bcatalysts?\b.{0,100}\b(?:synthesized|synthesised|fabricated|prepared)\b", sentence)
            and has(r"\b(?:demonstrated|experimentally (?:tested|evaluated)|measured)\b", sentence))
        validated_workflow = (
            has(r"\bwe (?:present|report|demonstrate)\b.{0,90}\bexperimentally validated\b", sentence)
            and has(r"\b(?:workflow|framework)\b", sentence)
            and has(r"(?:reaction[- ]condition|condition|catalyst).{0,50}recommendation|model[- ]guided (?:proposals|design)", sentence))
        if closed_loop or guided_synthesis or validated_workflow:
            return True
    return False


def electrical_catalyst_platform(title, abstract):
    """Electric heat is relevant, but does not itself prove resistive/Joule heat."""
    phrase = r"electrically[- ]heated (?:catalysts?|catalytic (?:beds?|reactors?))|electric[- ]heating (?:of|for) (?:catalysts?|catalytic (?:beds?|reactors?))"
    if has(phrase, norm(title)):
        return True
    return any(has(r"\b(?:we|it|this (?:paper|work|study)).{0,40}(?:review|investigat|stud(?:y|ies)|develop|use|evaluat|demonstrat).{0,100}(?:" + phrase + r")", s)
               for s in current_method_sentences(abstract))


def programmable_temperature_context(title, abstract):
    """A local thermal-method cue, not whole-abstract chemical co-occurrence.

    Read the original input: positive_context intentionally removes some
    comparison clauses, whereas comparing TPR methods can be positive evidence.
    Commas are retained for specimen apposition; independent contrast clauses
    are separated. This is bounded prescreening, not a dependency parser.
    """
    thermal = (r"(?:temperature[- ]program\w*|heating[- ]program\w*|"
               r"programm\w* (?:reaction )?(?:temperature|heating)|"
               r"temperature (?:was |is |were |are )?programm\w*|"
               r"(?:rapid |thermal )quench\w*|程序升温|温度程序|快速淬冷|"
               r"(?:反应|合金|材料|催化剂)温度.{0,12}周期)")
    technique = (r"(?:temperature[- ]programmed (?:(?:h2|nh3|co2|co|o2|hydrogen|ammonia|carbon dioxide) )?(?:reduction|desorption|oxidation)|"
                 r"\b(?:h2|nh3|co2|co|o2)[- ]tp[rod]\b|程序升温(?:还原|脱附|氧化)|"
                 r"\btp[rod]\b(?=.{0,45}(?:characteriz|reducibility|acid sites|tests?|cycles?)))")
    cue = "(?:" + thermal + "|" + technique + r"|\btp[rod]\b)"
    material = (r"(?:iron(?: oxide)?|steel|alloy\w*|metal\w*|oxide\w*|zeolite\w*|"
                r"catalyst\w*|ceramic\w*|polymer\w*|carbon(?: support\w*)?|"
                r"fe2o3|feo\w*|cuo|materials?|nanoparticle\w*|"
                r"合金|金属|材料|催化剂|氧化物|碳载体|钢)")
    reaction = r"(?:(?:chemical|catalytic) reaction|catalytic reactor|catalyst bed|反应温度|催化床|催化反应)"
    program = "(?:" + thermal + ")"
    # An attached object, rather than a chemical word elsewhere in a sentence.
    relations = (
        program + r"(?: of)?(?: the)? (?:" + material + r"|" + reaction + r")",
        program + r" (?:quench\w*|pyrolys\w*|anneal\w*|sinter\w*|carboniz\w*)(?: of)?\b",
        program + r" (?:controls?|modulat\w*) (?:the )?(?:phase transformation|carbon support preparation|material\w* (?:synthesis|processing))",
        material + r"(?: sample| specimen)?\s*,?\s*(?:under|subjected to|following|follows) (?:a |the )?" + program,
        reaction + r".{0,30}(?:under|during|with) (?:a |the )?" + program,
        r"(?:temperature of (?:a |the )?" + reaction + r"|reaction temperature).{0,20}programm\w*",
        r"programm\w* (?:the )?temperature of (?:a |the )?" + reaction,
        program + r".{0,14}(?:调控|调制|控制).{0,12}(?:" + material + r"|反应)",
        r"(?:对)?(?:" + material + r"|反应)温度.{0,14}(?:周期|程序|升温)",
    )

    def clauses(value):
        return [s.strip() for s in re.split(
            r"[。；;!?]|(?<!\d)\.(?!\d)|\b(?:but(?! also\b)|while|whereas|however)\b|"
            r"(?:,\s*)?\band\s+(?=(?:future work|we will|we plan to)\b)|但是|然而",
            norm(value)) if s.strip()]

    def withheld(s):
        # Negation must concern the thermal cue or its execution, not a later
        # unrelated object. 'not only' is an affirmative construction.
        s = re.sub(r"\bnot only\b", "also", s)
        return (has(r"\b(?:previous|prior|earlier) (?:studies|work)|\bbackground\b|unrelated appendix", s)
                or has(r"(?:\bno |\bnot (?:perform\w*|use\w*|conduct\w*|measur\w*|study|studied|test\w*)\s+)(?:(?:a|an|the) )?" + cue, s)
                or has(cue + r".{0,55}\b(?:was|were|is|are) not (?:perform\w*|use\w*|conduct\w*|measur\w*|studied|test\w*)", s)
                or has(r"\b(?:plan\w* to|will|intend\w* to|propos\w* to)\b.{0,55}" + cue, s)
                or has(cue + r".{0,70}\b(?:will be|is planned|are planned|future (?:work|studies)|next year|not yet)\b", s)
                or has(r"(?:未|没有|不)(?:实施|进行|采用|开展|测量|测试).{0,15}" + cue, s)
                or has(cue + r".{0,25}(?:未实施|尚未进行|计划未来|将进行)", s))

    def kind(s):
        if has(technique, s):
            return "characterization"
        # Optical quenching acts on emission, not on the material's thermal
        # history. Do not erase actual material quenching merely because later
        # optical measurements are mentioned in the same clause.
        if has(r"quench\w* (?:of )?(?:the )?(?:(?:" + material + r")[- ]?){0,2}(?:luminescence|fluorescence|photoluminescence|phosphorescence|emission)\b", s):
            return None
        # TPD and solid-state quenching need not involve chemical reaction.
        # Only a claimed reaction object is negated by this phrase.
        s = re.sub(r"(?:without|no) (?:chemical|catalytic) reaction|不涉及化学反应", "", s)
        if not has(program, s) or not any(has(rx, s) for rx in relations):
            return None
        if has(r"reduction peaks|acid sites|reducibility|desorption peaks|还原峰|脱附峰", s):
            return "characterization"
        if has(reaction, s) or has(r"反应温度|合成氨|\bammonia synthesis\b", s):
            return "reaction"
        return "material"

    abstract_clauses = clauses(abstract)
    # A positive current-method clause wins over an unrelated negative clause;
    # otherwise explicit nonperformance/future work must override the title.
    for s in abstract_clauses:
        if not withheld(s):
            found = kind(s)
            if found:
                return found
    if any(has(cue, s) and withheld(s) for s in abstract_clauses):
        return None
    if any(not withheld(s) and has(r"\bonly isothermal (?:experiments?|measurements?|treatments?)\b", s)
           for s in abstract_clauses):
        return None
    if any(has(r"\bno thermal (?:treatment|processing)\b.{0,25}(?:perform|conduct|use)|"
               r"thermal (?:treatment|processing).{0,15}not (?:perform|conduct|use)", s)
           for s in abstract_clauses):
        return None
    for s in clauses(title):
        if not withheld(s):
            found = kind(s)
            if found:
                return found
    return None


def classify(title, abstract=None, evidence_kind=None):
    title = plain(title); abstract = plain(abstract)
    t = norm(title); a = positive_context(norm(abstract)); text = t + " ; " + a
    topics=[]; flags=[]
    def topic(x):
        if x not in topics: topics.append(x)
    ald_full=has(r"atomic[- ]layer[- ]deposition|原子层沉积", text)
    ald=ald_full or (has(r"\bald\b",text) and has(r"ruthenium|\bru\b|carbon|nucleat|overcoat|催化|包覆|成核|catalyst",text))
    medical=has(r"alcoholic liver|adrenoleukodystrophy|biomarker|gene therapy|pharmacogenom|human.{0,45}(?:prostaglandin|paralog)|肝病|遗传疾病|药物基因组",t)
    # Joule dissipation in generic stretching-fluid models is not a catalytic
    # Joule-heated bed. Keep these records searchable, outside the default queue.
    mhd_noise=has(r"nanofluid",t) and has(r"stretch|\bmhd\b|magnetohydro",t) and not has(r"cataly|ammonia|atomic.layer",t)
    irrelevant=medical or mhd_noise or has(r"protein folding|stock.trading|stock price|refrigeration|冷媒|制冷|蛋白.*结构|autonomous driving|corium",t)
    semiconductor=ald_full and has(r"commercial memory|memory.chip|gate dielectric|photodetector|transparent electronics|芯片制程",t) and not has(r"nucleation|cataly|carbon|成核",t)
    ald_method_background=semiconductor and has(r"atomic layer annealing|deposition pathways|three ald processes|nucleation mechanism",text)
    joule=has(r"joule|direct (?:electrical )?resisti(?:ve|vity)|电阻加热|焦耳热",text)
    # Never let an unbounded nitrogen.*reduction span a whole abstract: N doping
    # followed paragraphs later by carbonate reduction is not ammonia synthesis.
    ammonia=has(r"ammonia (?:synthesis|production)|synthesis of ammonia|nitrogen[- ](?:reduction|fixation)|reduction of nitrogen|合成氨|制氨",text)
    ammonia_any=has(r"\bammonia\b|\bnh\s*3\b|制氨|合成氨",text)
    electro=has(r"electrochemi|electrocatal|electroreduc|electrooxid|electrosynth|电化学|电催化|electrolyte|电解液",t + " ; " + a)
    photo=has(r"photocatal|photoelectrocatal|光催化|under visible light|light.driven",text)
    n2_source=has(r"\bn\s*2\b|nitrogen (?:fixation|reduction)|reduction of nitrogen|固氮|氮还原",text)
    broad_review=has(r"review|recent advances|综述",t) or has(r"(?:this|the present) review|review article|(?:this|the present) paper reviews",a)
    measurement_method=primary_measurement_method(title, abstract)
    catalytic_data=catalytic_data_method(title)
    ml_experiment = catalytic_data and not broad_review and evidence_kind in (None, "abstract") and catalytic_ml_experiment_optimization(abstract)
    ml_mixed_title_only = catalytic_data and not abstract and has(r"experimental validation", t)
    electrical_platform=electrical_catalyst_platform(title, abstract)
    multi_reaction_review=broad_review and not has(r"ammonia|nitrogen|haber|氨|固氮",t) and has(r"applications span|various reactions|multiple reactions",a)
    process="unknown"
    if irrelevant: process="no_target_chemical_process"
    elif multi_reaction_review: process="general_catalysis"
    elif has(r"urea synthesis|尿素合成|ammonia recovery|ammonia separation|氨回收",t): process="other_catalytic_reaction"
    elif has(r"ammonia borane|氨硼烷",text): process="ammonia_borane_hydrolysis"
    elif has(r"ammonia (?:decomposition|cracking)|氨(?:分解|裂解)|decomposition of ammonia",text): process="ammonia_decomposition"
    elif has(r"dry reforming.*methane|methane.*dry reforming|甲烷干重整",text): process="methane_dry_reforming"
    elif has(r"reverse water.gas shift|\brwgs\b|逆水煤气",text): process="reverse_water_gas_shift"
    elif ammonia_any and has(r"mechanochemi|mechanocatal|机械化学",t): process="mechanochemical_ammonia"
    elif ammonia_any and has(r"chemical.looping|nitrogen carrier|lattice nitrogen|化学链|晶格氮",text): process="chemical_looping_ammonia_synthesis"
    elif ammonia and has(r"plasma|等离子体",text): process="plasma_ammonia_synthesis"
    elif ammonia_any and photo:
        process="photocatalytic_nitrate_reduction" if has(r"nitrate|(?<!亚)硝酸盐",text) else "photocatalytic_n2_reduction" if n2_source else "ammonia_route_unspecified"
    elif electro and has(r"nitrate|(?<!亚)硝酸盐",text): process="electrochemical_nitrate_reduction"
    elif electro and has(r"nitrite|亚硝酸盐",text): process="electrochemical_nitrite_reduction"
    elif electro and (ammonia or (ammonia_any and n2_source)): process="electrochemical_n2_reduction" if n2_source else "ammonia_route_unspecified"
    elif has(r"hydrogen evolution|\bher\b|析氢",text): process="hydrogen_evolution"
    elif ammonia or has(r"haber.?bosch",text):
        # Background Haber–Bosch mentions are motivation, not proof of feed.
        active_parts=re.split(r"\b(?:herein|here we|in this (?:study|work)|we (?:report|show|develop|propose))\b",a,maxsplit=1)
        route_text=t+" ; "+(active_parts[-1] if len(active_parts)>1 else a)
        route_text=" ; ".join(c for c in re.split(r"[;.]",route_text) if not has(r"traditional|conventional|alternative to|reliance on|传统|常规",c))
        gas=has(r"haber.?bosch|gas.phase|n\s*2\s*(?:/|and|[+:])\s*h\s*2|nitrogen and hydrogen|气相.*(?:合成氨|制氨)|(?:n2.{0,10}h2).*合成氨|thermo.?catalytic|thermochemical",route_text)
        # HB in an introduction, reference list, or cross-sector analogy does
        # not establish the primary reaction. A non-target title needs an
        # explicit present-work ammonia-synthesis statement, not a keyword.
        title_target=has(r"ammonia|haber.?bosch|合成氨|制氨",t)
        active_target=len(active_parts)>1 and has(r"ammonia (?:synthesis|production)|synthesis of ammonia|合成氨",active_parts[-1])
        if gas and not (title_target or active_target):
            gas=False;flags.append('background_route_mention_not_primary_target')
        if has(r"microdroplet|nanobubble|微液滴|纳米气泡",t):gas=False;flags.append('non_bed_ammonia_route_requires_method_review')
        process="gas_n2_h2_ammonia_synthesis" if gas else "ammonia_route_unspecified"
    elif has(r"dye degradation|污染物降解|oxidation|hydrogenation|reforming|h2o2 dissociation|hydrogen peroxide|(?:co2|carbon dioxide).{0,30}(?:reduc|conversion)",text): process="other_catalytic_reaction"
    elif ald and has(r"substrate|nucleat|成核",t): process="materials_synthesis_only"
    elif has(r"catalytic reactor|catalysis|催化反应器",text): process="general_catalysis"
    elif ald or has(r"flash joule|graphiti|nanoparticle|nanostructur|nanozyme|materials|载体|材料制备",text): process="materials_synthesis_only"
    heating="not_stated"
    programmable_context = programmable_temperature_context(title, abstract)
    prep=joule and (has(r"flash joule|焦耳热.{0,15}制备|电热.{0,15}制备|joule.{0,30}(?:prepar|conversion|synthesi[sz]ed)|joule.{0,30}(?:catalyst|material|nanoparticle).{0,15}synthesis|joule.{0,30}synthesis of (?!ammonia)(?:catalyst|material|nano)|先用焦耳热|只用于.*制备",text) or has(r"graphiti|(?:oxygen.vacancy|defect).{0,20}(?:modulat|engineer)|construct|sinter|anneal|\bcarboniz",t))
    if irrelevant: heating="none_relevant"
    elif prep: heating="preparation_joule"
    elif process.startswith("electrochemical"): heating="electrochemical_not_joule"
    elif process.startswith("photocatalytic"): heating="light_driven"
    elif photo and (ammonia_any or multi_reaction_review): heating="light_driven"
    elif electro: heating="electrochemical_not_joule"
    elif process.startswith("plasma"): heating="plasma_driven"
    elif process=="mechanochemical_ammonia": heating="mechanical_driven"
    elif joule and (has(r"reactor|reaction|reforming|water.gas|heated|加热驱动|催化床|反应|direct joule heating",text)):
        heating="reaction_joule"
    elif programmable_context and not (programmable_context == "characterization"
            and has(r"electric.field|电场辅助", text)
            # Preserve the pre-R8 priority for already-recognized full thermal
            # methods, but newly recognized TPR abbreviations cannot displace
            # an existing explicit electric-field driver.
            and not has(r"(?:temperature|heating).{0,20}(?:program|quench)|(?:程序升温|快速淬冷|温度.*周期)", text)):
        heating="reaction_programmable"
    elif has(r"electric.field|电场辅助",text): heating="electric_field_not_joule"

    use="unclear"
    if irrelevant or (semiconductor and not ald_method_background): use="unrelated"
    # Model/output-bias calibration is not instrument or measurement evidence.
    # Require a local instrument context for calibration; retain explicit
    # thermometry/isotope controls as independent measurement signals.
    elif measurement_method or has(r"emissivity|hot spots|thermometr|isotop|15n2|同位素|发射率|测温|氮来源|空白",text) or has(r"(?:calibrat\w*|校准|标定)[^.;；。]{0,55}(?:thermocouple|pyromet|sensor|instrument|detector|mass spectromet|temperature measurement|热电偶|传感器|测温仪)|(?:thermocouple|pyromet\w*|sensor|instrument|detector|mass spectromet\w*|temperature measurement|热电偶|传感器|测温仪)[^.;；。]{0,55}(?:calibrat|校准|标定)",text): use="measurement_validation"
    elif has(r"active learning|bayesian|主动学习|贝叶斯",text) or has(r"machine.learning.{0,30}screen|optimal sizing",t): use="research_optimization"
    elif broad_review: use="review_background"
    elif ml_experiment: use="research_optimization"
    elif ml_mixed_title_only: use="unclear"
    elif catalytic_data: use="kinetics_model"
    elif has(r"reactor|process design|operating envelope|space velocity|washcoated|导电泡沫|techno.economic",t): use="reactor_design"
    # A supporting DFT paragraph does not make an experimental catalyst paper
    # primarily a modelling paper. Explicit modelling titles remain modelling.
    elif has(r"\bdft\b|microkinetic|kinetic|molecular dynamics|statistical.mechanical|force field|computational approach|\bsimulation study\b|\bprocess simulation\b|transfer learning|yield prediction|动力学|建模",t) or has(r"(?:we|herein|here).{0,65}(?:simulation method|computational simulations|ab initio)|computational simulations reveal",a): use="kinetics_model"
    elif process=="ammonia_borane_hydrolysis" and not (ald or prep or has(r"prepar|synthesi[sz]|制备",t)): use="reaction_operation"
    elif ald or prep or has(r"promoter|助剂|nucleat|负载|nanocluster|nanoparticle|制备|overcoat",t): use="catalyst_preparation"
    elif process not in ("unknown","no_target_chemical_process","materials_synthesis_only","ammonia_route_unspecified"): use="reaction_operation"

    adjacent={"ammonia_decomposition","ammonia_borane_hydrolysis","electrochemical_n2_reduction","electrochemical_nitrate_reduction","electrochemical_nitrite_reduction","chemical_looping_ammonia_synthesis","photocatalytic_n2_reduction","photocatalytic_nitrate_reduction","plasma_ammonia_synthesis","mechanochemical_ammonia"}
    general_history=has(r"historical|history of|历史|capitalism|food security|mission notebook",t) and not has(r"joule|atomic.layer|焦耳|原子层",t)
    if irrelevant or (semiconductor and not ald_method_background): tier="exclude"
    elif ald_method_background:tier="background";use="catalyst_preparation";flags.append('electronics_ald_method_not_catalysis_performance')
    elif general_history: tier="background"; process="general_catalysis"
    elif process=="gas_n2_h2_ammonia_synthesis": tier="cross" if (ald or heating in ("reaction_joule","preparation_joule","reaction_programmable")) else "direct"
    elif process=="ammonia_route_unspecified": tier="uncertain"
    elif ald or heating in ("reaction_joule","preparation_joule","reaction_programmable"): tier="transferable"
    elif process in adjacent: tier="background"
    elif multi_reaction_review or (electro and process=="other_catalytic_reaction"): tier="background"
    elif electrical_platform or (catalytic_data and (use in ("kinetics_model", "review_background", "research_optimization") or ml_mixed_title_only)): tier="transferable"
    elif use in ("measurement_validation","research_optimization") and has(r"cataly|reactor|ammonia|reaction|催化|反应器",text): tier="transferable"
    elif process=="unknown": tier="uncertain" if has(r"\bald\b|ammonia|焦耳|氨",t) else "exclude"
    else: tier="exclude"
    # Primary-object and content-kind gate. These are semantic patterns, not
    # DOI/title allowlists. Preserve adjacent information without counting it
    # as new gas-phase catalytic performance. The input remains title+abstract.
    biology=has(r"biological nitrogen fixation|bacterial nitrogen fixation|nitrogenase|femo[- ]?cofactor|whole.cell biocatalysis|生物固氮",t)
    supply_context=has(r"air separation|nitrogen production|nitrogen budget|nitrogen management|ammoni(?:a|um).{0,25}(?:adsorpti|sensing|recovery|separation)|fertilizer.dependent food|nutrient.{0,20}(?:allocation|circularity)|氨(?:回收|吸附)|氮收支",t)
    teaching=has(r"(?:education|teaching|scaffolded|tutorial).{0,40}(?:module|process)|module.{0,70}(?:education|process simulation)|engineering education|^critique\b|教学|教程",t)
    essay=has(r"nobel prize|ontological|epistemolog|historical.{0,20}essay",t) or has(r"this is a historical.{0,35}essay|not a report of original empirical research",a)
    general_chemistry=has(r"industrial manufacturing of acids|acids and base synthesis",t)
    supporting_data=has(r"^(?:abstract\s+)?(?:data,? (?:input files|code)|(?:data|code) and (?:input files|instructions)|supporting (?:data|information)|dataset (?:for|accompanying))",norm(abstract))
    system_assessment=has(r"techno.economic|energy analysis|optimal sizing|energy storage system|hexageneration|distribution networks|waste.heat recovery|environmental assessment",t) and not has(r"reactor|kinetic|yield prediction",t)
    broad_axiomatic_framework=has(r"axiomatic framework|公理.{0,8}框架",t) and has(r"thermodynamic|catalysis|热力学",t)
    noncatalytic_corrosion=ald and has(r"corrosion.{0,20}(?:barrier|coating)|anticorrosion|防腐|防蚀",t) and not has(r"cataly|reactor|electrode|氨|催化|反应器|电极",text)
    if supporting_data:
        tier="background";use="review_background";flags.append('supporting_dataset_not_independent_paper')
    elif biology:
        tier="background";process="biological_nitrogen_fixation";heating="not_stated"
        use="review_background" if broad_review else "unclear";flags.append('biological_route_not_gas_n2_h2')
    elif supply_context:
        tier="background";process="ammonia_supply_chain_context";heating="not_stated"
        use="measurement_validation" if has(r"sensing|detect|测量|检测",t) else "review_background"
        flags.append('upstream_downstream_context_not_synthesis_result')
    elif teaching or essay or general_chemistry:
        tier="background";process="general_catalysis";heating="not_stated";use="review_background"
        flags.append('education_history_or_general_context')
    elif system_assessment and process in ("gas_n2_h2_ammonia_synthesis","ammonia_route_unspecified"):
        tier="background";use="reactor_design";flags.append('plant_energy_economics_not_catalyst_performance')
    elif broad_axiomatic_framework and tier in ("direct","cross"):
        tier="uncertain";use="kinetics_model";flags.append('broad_theoretical_framework_requires_method_review')
    elif noncatalytic_corrosion:
        tier="background";flags.append('ald_protective_coating_catalytic_transfer_not_established')
    if measurement_method and use=="measurement_validation": flags.append('primary_measurement_method')
    if catalytic_data and tier!="exclude": flags.append('catalytic_data_method_transfer')
    if ml_experiment and use=="research_optimization": flags.append('catalytic_ml_experiment_optimization')
    if ml_mixed_title_only and use=="unclear": flags.append('catalytic_ml_mixed_title_only')
    if electrical_platform and heating=="not_stated" and tier!="exclude": flags.append('electrical_heating_mechanism_unresolved')
    # Preserve relevance/chemical-route decisions. This narrow correction only
    # changes the default reaction-operation description when the present work
    # is explicitly an analytical stoichiometric-bound calculation.
    stoichiometric_bound = use == "reaction_operation" and primary_stoichiometric_bound(title, abstract)
    if stoichiometric_bound:
        use="kinetics_model";flags.append('stoichiometric_bound_not_measured_reaction')
    # Relevance is a recommendation axis, not a reason to erase an observed
    # scientific process/method. Irrelevant-domain guards above still apply.
    if ald and not medical: topic("ald")
    if process in ("gas_n2_h2_ammonia_synthesis","ammonia_route_unspecified"): topic("ammonia_synthesis")
    if process in adjacent: topic("ammonia_adjacent")
    if process=="ammonia_decomposition": topic("ammonia_decomposition")
    if joule and not irrelevant: topic("joule")
    if heating=="preparation_joule": topic("joule_preparation")
    if heating=="reaction_joule": topic("joule_reaction")
    if heating=="reaction_programmable": topic("programmable_heating_quenching")
    if tier!="exclude":
        for key,rx in {"ru":r"\bru\b|ruthenium|钌", "iron":r"\bfe\b|\biron\b|铁", "carbon":None, "kinetics":r"kinetic|动力学", "dft":r"\bdft\b|密度泛函", "active_learning":r"active learning|主动学习", "bayesian_optimization":r"bayesian|贝叶斯", "reactor":r"reactor|反应器|space velocity", "thermometry":r"thermometr|emissivity|测温|发射率", "stability":r"stabil|稳定", "interface":r"interface|界面", "nucleation":r"nucleat|成核", "review":r"review|综述"}.items():
            matches = carbon_material(text) if key=="carbon" else has(rx,text)
            if matches: topic(key)
    basis=(evidence_kind or "abstract") if abstract else "title"
    if basis not in ("abstract","feed_fragment","source_text_unverified","title"):basis="source_text_unverified"
    source_label={"abstract":"题名与来源摘要","feed_fragment":"题名与订阅描述片段","source_text_unverified":"题名与未核实完整性的来源文本","title":"题名"}[basis]
    level={"abstract":"摘要级（规则预筛，未阅读全文）","feed_fragment":"订阅描述片段（不等于完整摘要，未阅读全文）","source_text_unverified":"来源文本（完整性/类型尚未核实，未阅读全文）","title":"标题级（结果与条件尚待核实）"}[basis]
    material_names=[label for key,label in [("ru","Ru/钌体系"),("iron","Fe/铁体系"),("carbon","碳材料"),("ald","原子层沉积")] if key in topics]
    obj=PROCESSES[process] + ("；涉及"+"、".join(material_names) if material_names else "")
    heating_label = HEATING[heating]
    if heating == "reaction_programmable":
        heating_label = {"characterization": "表征温程（TPR/TPD/TPO 等，不等于反应供热）",
                         "material": "材料温程/热淬冷（不等于反应供热）",
                         "reaction": "反应温度程序（供热机制待核）"}[programmable_context]
        if use == "kinetics_model":
            heating_label = "模型中的" + heating_label + "；不等于实测温程或催化性能"
    method=USES[use]+"；"+heating_label
    unknown="数值性能、机制解释、具体反应条件及迁移适用性仍需阅读原文核实。"
    if tier=="uncertain": unknown="当前证据不足以确认主研究对象、路线或方法适用性；请先补摘要/核查方法，不应按直接命中计数。"
    if stoichiometric_bound:
        method="理论物料衡算 / 化学计量上界（非反应实测）；"+heating_label
        unknown+=" 此处理论界限不代表实测产率、反应动力学预测或装置性能；需另据原始方法和实验核查。"
    if 'catalytic_data_method_transfer' in flags:
        method+="；催化数据模型/筛选方法，模型输出与实验观测须分开核对"
        unknown+=" 数据来源、泄漏/外推验证和面向本课题的适用性须另行核查；模型分数不能代替催化实验验证。"
        if 'catalytic_ml_experiment_optimization' in flags:
            method+="；摘要报告模型指导的候选/条件选择及实验验证"
            unknown+=" 摘要中的实验报告尚未独立核验；不能据此认定适用于本课题或性能结论已成立。"
        elif 'catalytic_ml_mixed_title_only' in flags:
            method+="；题名同时提到机器学习与实验验证，主用途和实际实验范围待核"
            unknown+=" 未取得摘要，不推断仅为理论研究，也不把题名作为实验已完成的证据。"
    if 'electrical_heating_mechanism_unresolved' in flags:
        method+="；电加热催化平台候选，具体电热耦合机制未定"
        unknown+=" 电加热线索不能据此认定焦耳/电阻供热，不能当作气相合成氨实测证据。"
    reason={"direct":"规则发现气相N₂/H₂合成氨相关线索；仅为待审候选，不证明研究质量或性能有效。", "cross":"规则发现目标反应与精准调控/热程序的交叉线索；需核对主研究对象、方法及证据。", "transferable":"可关注材料制备、供热或测量设计是否可迁移；不代表已证明对目标反应有效。", "background":"邻近反应、配套环节或学习材料；不能计作新增气相合成氨催化性能论文。", "uncertain":"保留为待核候选，避免仅凭宽泛题名漏掉可能重要的研究。", "exclude":"当前可见证据没有足够的课题关联，不进入默认阅读队列。"}[tier]
    known=f"依据{source_label}，可见对象线索为{obj}，方法线索为{method}。"
    content_notes=source_content_notes(title, abstract, supporting_data)
    if content_notes['content_kind'] != 'unspecified':
        known += " 内容形式提示："+content_notes['content_kind_label']+"。"+content_notes['content_kind_note']
    if content_notes['quality_notes']:
        unknown += " "+content_notes['quality_notes'][0]['message']
    summary=known+" "+unknown
    return {"tier":tier,"tier_zh":TIERS[tier],"process":process,"process_zh":PROCESSES[process],
            "heating":heating,"heating_zh":heating_label,"use":use,"use_zh":USES[use],"topics":topics,
            "score":{"cross":95,"direct":90,"transferable":65,"uncertain":50,"background":35,"exclude":0}[tier],
            "summary_zh":summary,"reason_zh":reason,"evidence_level":basis,"evidence_label":level,
            "interpretation_method":"rule_prescreen","score_meaning":"fixed_triage_priority_not_confidence_or_quality","structured_summary":{"object":obj,"question":"理论物料衡算 / 化学计量上界" if stoichiometric_bound else USES[use],"method":heating_label,"known":known,"unknown":unknown},
            "rule_version":RULE_VERSION,"input_sha256":hashlib.sha256((title+"\n"+abstract).encode()).hexdigest(),"flags":flags,**content_notes}
