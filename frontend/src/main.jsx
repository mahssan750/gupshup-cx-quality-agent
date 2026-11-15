import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API = "/api/v1";

const icons = {
  home: "M3 11.5 12 4l9 7.5V21a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z",
  evidence: "M9 3h6l1 2h3v17H5V5h3zm0 7h6m-6 4h6m-6 4h4",
  knowledge: "M4 4.5A3.5 3.5 0 0 1 7.5 1H20v17H7.5A3.5 3.5 0 0 0 4 21.5zm0 17A3.5 3.5 0 0 1 7.5 18H20",
  runs: "M12 2a10 10 0 1 0 10 10M12 6v6l4 2",
  spark: "m12 3 1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6zM5 15l.8 2.2L8 18l-2.2.8L5 21l-.8-2.2L2 18l2.2-.8z",
  arrow: "m9 18 6-6-6-6",
  folder: "M3 6h7l2 2h9v11H3z",
  check: "m5 12 4 4L19 6",
  alert: "M12 3 2.5 21h19zM12 9v5m0 3v.01",
  search: "m21 21-4.3-4.3M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  menu: "M4 7h16M4 12h16M4 17h16",
  close: "m6 6 12 12M18 6 6 18",
  sun: "M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  moon: "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79",
  lock: "M7 11V8a5 5 0 0 1 10 0v3m-11 0h12v10H6z",
  logout: "M10 17l5-5-5-5m5 5H3m12-8h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5",
};

function Icon({ name, size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={icons[name]} />
    </svg>
  );
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || "تعذر إتمام الطلب");
    error.status = response.status;
    if (response.status === 401 && !path.endsWith("/auth/login")) {
      window.dispatchEvent(new Event("cxquality:unauthorized"));
    }
    throw error;
  }
  return data;
}

function shortId(value = "") {
  return value.slice(0, 8);
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ar-EG", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function money(value) {
  return `$${Number(value || 0).toFixed(4)}`;
}

const statusText = {
  queued: "في قائمة الانتظار",
  running: "جاري التحليل",
  completed: "مكتمل",
  blocked: "متوقف مؤقتًا",
  stopped_budget: "توقف عند حد التكلفة",
  failed: "فشل التشغيل",
};

const categoryMeta = {
  non_compliant: { label: "مخالفة", tone: "bad" },
  compliant: { label: "ملتزم", tone: "good" },
  needs_rule: { label: "يحتاج قاعدة", tone: "warn" },
  not_scorable: { label: "خارج النطاق", tone: "muted" },
};

function Brand() {
  return (
    <div className="brand">
      <div className="brandMark"><span>dn</span><i /></div>
      <div><strong>Dr. Nutrition</strong><small>Quality intelligence</small></div>
    </div>
  );
}

function LoginScreen({ theme, toggleTheme, onLogin }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setLoading(true); setError("");
    try {
      const result = await request(`${API}/auth/login`, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      onLogin(result);
    } catch (loginError) {
      setError(loginError.message);
    } finally {
      setLoading(false);
    }
  }
  return <main className="loginPage" dir="rtl">
    <button className="themeToggle loginTheme" type="button" onClick={toggleTheme} aria-label="تغيير المظهر">
      <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
      <span>{theme === "dark" ? "فاتح" : "داكن"}</span>
    </button>
    <section className="loginCard">
      <Brand />
      <div className="loginLock"><Icon name="lock" size={30} /></div>
      <span className="loginEyebrow">منطقة المشرف</span>
      <h1>تسجيل الدخول</h1>
      <p>أدخل بيانات المسؤول للوصول إلى التشغيلات، أحكام المشرف وقاعدة المعرفة.</p>
      <form onSubmit={submit}>
        <label htmlFor="login-username">اسم المستخدم</label>
        <input id="login-username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required autoFocus />
        <label htmlFor="login-password">كلمة المرور</label>
        <input id="login-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
        {error && <div className="formMessage bad loginError">{error}</div>}
        <button className="primaryAction" disabled={loading}>
          {loading ? <span className="spinner" /> : <Icon name="lock" size={18} />}
          {loading ? "جاري التحقق..." : "دخول آمن"}
        </button>
      </form>
      <small><Icon name="lock" size={13} /> جلسة محلية محمية وتنتهي تلقائياً.</small>
    </section>
  </main>;
}

function Navigation({ active, setActive, open, setOpen }) {
  const items = [
    ["home", "مركز التحكم", "الملخص والتشغيل"],
    ["evidence", "مختبر الأدلة", "مراجعة العينات"],
    ["knowledge", "دورة المعرفة", "القواعد والمسودات"],
    ["runs", "سجل التشغيل", "التاريخ والتكلفة"],
  ];
  return (
    <aside className={`sidebar ${open ? "open" : ""}`}>
      <div className="sideTop"><Brand /><button className="iconButton mobileOnly" onClick={() => setOpen(false)}><Icon name="close" /></button></div>
      <nav>
        {items.map(([id, label, sub]) => (
          <button key={id} className={active === id ? "active" : ""} onClick={() => { setActive(id); setOpen(false); }}>
            <span className="navIcon"><Icon name={id === "home" ? "home" : id} /></span>
            <span><b>{label}</b><small>{sub}</small></span>
            <Icon name="arrow" size={15} />
          </button>
        ))}
      </nav>
      <div className="engineStamp">
        <span className="liveDot" />
        <div><b>Company Supervisor</b><small>المحرك الوحيد الفعّال</small></div>
      </div>
    </aside>
  );
}

function Header({ title, subtitle, setMenu, theme, toggleTheme, username, onLogout }) {
  return (
    <header className="topbar">
      <button className="iconButton mobileOnly" onClick={() => setMenu(true)}><Icon name="menu" /></button>
      <div><h1>{title}</h1><p>{subtitle}</p></div>
      <div className="headerActions">
        <button className="themeToggle" type="button" onClick={toggleTheme} aria-label={theme === "dark" ? "تفعيل الوضع الفاتح" : "تفعيل الوضع الداكن"} title={theme === "dark" ? "الوضع الفاتح" : "الوضع الداكن"}>
          <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
          <span>{theme === "dark" ? "فاتح" : "داكن"}</span>
        </button>
        <button className="logoutButton" type="button" onClick={onLogout} title="تسجيل الخروج"><Icon name="logout" size={17} /><span>{username || "admin"}</span></button>
        <div className="systemState"><span className="liveDot" /> النظام جاهز</div>
      </div>
    </header>
  );
}

function RunLauncher({ onCreated, overview }) {
  const [folder, setFolder] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(null);
  const active = overview?.control?.active_runs > 0;
  async function submit(event) {
    event.preventDefault();
    setLoading(true); setMessage(null);
    try {
      const data = await request(`${API}/runs`, { method: "POST", body: JSON.stringify({ zip_folder: folder }) });
      setMessage({ good: true, text: `بدأ التشغيل ${shortId(data.run_id)} على ${data.zip_count} محادثة.` });
      setFolder("");
      onCreated(data.run_id);
    } catch (error) {
      setMessage({ good: false, text: error.message });
    } finally { setLoading(false); }
  }
  return (
    <section className="runLauncher">
      <div className="orb orbOne" /><div className="orb orbTwo" />
      <div className="launcherCopy">
        <span className="eyebrow"><Icon name="spark" size={16} /> تشغيل ذكي جديد</span>
        <h2>ضع مسار ملفات ZIP.<br /><em>والباقي علينا.</em></h2>
        <p>لا تواريخ، لا معايير عامة، ولا إعدادات مربكة. المحرك يستخدم فقط أحكام المشرفين وقواعد الشركة المعتمدة.</p>
      </div>
      <form onSubmit={submit} className="launcherForm">
        <label htmlFor="zip-folder">مجلد ملفات المحادثات</label>
        <div className="pathInput">
          <Icon name="folder" />
          <input id="zip-folder" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="C:\\exports\\weekly-zips" required />
        </div>
        <button className="primaryAction" disabled={loading || active}>
          {loading ? <span className="spinner" /> : <Icon name="spark" />}
          {active ? "يوجد تشغيل نشط" : loading ? "جاري البدء..." : "ابدأ التحليل الكامل"}
        </button>
        <small>حدود التكلفة والنماذج تُقرأ تلقائيًا من إعدادات المشروع.</small>
        {message && <div className={`formMessage ${message.good ? "good" : "bad"}`}>{message.text}</div>}
      </form>
    </section>
  );
}

function RunPulse({ run, coverage, onRunAction }) {
  if (!run) return <section className="emptyPanel">لا توجد تشغيلات بعد. ابدأ من المربع أعلاه.</section>;
  const processed = Number(run.processed_count || 0);
  const total = Number(run.total_count || 0);
  const progress = total ? Math.min(100, Math.round(processed / total * 100)) : run.status === "completed" ? 100 : 0;
  return (
    <section className="runPulse">
      <div className="sectionHeading">
        <div><span className="overline">LATEST RUN</span><h3>نبض التشغيل الأخير</h3></div>
        <div className="runStateActions"><span className={`statusBadge ${run.status}`}><i />{statusText[run.status] || run.status}</span>{["queued", "running"].includes(run.status) && <button onClick={() => onRunAction("stop", run.id)}>إيقاف آمن</button>}{["blocked", "stopped_budget", "failed"].includes(run.status) && <button onClick={() => onRunAction("resume", run.id)}>استئناف</button>}</div>
      </div>
      <div className="progressRail"><i style={{ width: `${progress}%` }} /></div>
      <div className="pulseStats">
        <div><b>{progress}%</b><span>نسبة الإنجاز</span></div>
        <div><b>{processed}<small> / {total || "—"}</small></b><span>محادثة معالجة</span></div>
        <div><b>{coverage?.scored || 0}</b><span>أحكام مكتملة</span></div>
        <div><b>{coverage?.needs_clarification || 0}</b><span>تحتاج قاعدة</span></div>
        <div><b>{money(run.cost_usd)}</b><span>التكلفة الفعلية</span></div>
      </div>
      <div className="runFoot"><span>RUN {shortId(run.id).toUpperCase()}</span><span>{formatDate(run.updated_at)}</span></div>
    </section>
  );
}

function KnowledgePulse({ knowledge, onOpen }) {
  const categories = knowledge?.categories || {};
  const rules = (categories["company-rule"] || 0) + (categories["company-supervisor-rule"] || 0) + (categories["quality-review"] || 0);
  return (
    <section className="knowledgePulse">
      <div className="knowledgeGraphic"><span>{knowledge?.active || 0}</span><small>عنصر معرفة فعّال</small></div>
      <div className="knowledgeCopy">
        <span className="overline">KNOWLEDGE LOOP</span>
        <h3>قاعدة تعرف شركتك أكثر مع كل تشغيل</h3>
        <p>اكتشفنا <b>{knowledge?.pending_candidates || 0}</b> مسودة تحتاج قرار مشرف. لا تُفعّل أي قاعدة تلقائيًا قبل اعتمادك.</p>
        <div className="knowledgeLine"><span><i className="green" />{rules} قاعدة وسياسة معتمدة</span><span><i className="purple" />{knowledge?.pending_candidates || 0} بانتظار المراجعة</span></div>
        <button className="textAction" onClick={onOpen}>راجع مسودات المعرفة <Icon name="arrow" size={16} /></button>
      </div>
    </section>
  );
}

function SamplePreview({ items, onOpen, onAll }) {
  return (
    <section className="samplePreview">
      <div className="sectionHeading">
        <div><span className="overline">REPRESENTATIVE SAMPLE</span><h3>لماذا اتخذ النموذج هذا الحكم؟</h3><p>عينة متوازنة للمراجعة السريعة، وليست الحالات الأسهل فقط.</p></div>
        <button className="quietAction" onClick={onAll}>فتح مختبر الأدلة</button>
      </div>
      <div className="sampleGrid">
        {items.slice(0, 3).map((item) => <CaseCard key={item.session_id} item={item} onOpen={onOpen} compact />)}
        {!items.length && <div className="emptyPanel">ستظهر العينة الممثلة هنا بعد اكتمال أول نتائج.</div>}
      </div>
    </section>
  );
}

function CaseCard({ item, onOpen, compact = false }) {
  const meta = categoryMeta[item.category] || categoryMeta.not_scorable;
  const customer = item.component_evidence?.customer || item.evidence?.[0];
  const agent = item.component_evidence?.agent || item.evidence?.[1];
  return (
    <article className={`caseCard ${compact ? "compact" : ""}`} onClick={() => onOpen(item)} tabIndex="0">
      <div className="caseTop"><span className={`verdict ${meta.tone}`}>{meta.label}</span>{item.reviewed ? <span className="reviewedBadge"><Icon name="check" size={12} /> تمت مراجعتها</span> : <span className="confidence">{item.confidence != null ? `${Math.round(item.confidence * 100)}% ثقة` : "بدون ثقة"}</span>}</div>
      <h4>{item.agent_name || "موظف غير مربوط"}</h4>
      <p className="caseReason">{item.explanation}</p>
      {customer && <blockquote><small>العميل</small>{String(customer).replace(/^.*Customer\s*:\s*/i, "")}</blockquote>}
      {!compact && agent && <blockquote className="agentQuote"><small>الموظف</small>{String(agent).replace(/^.*:\s*/, "")}</blockquote>}
      <div className="caseBottom"><span>{shortId(item.session_id)}</span><button>عرض الدليل <Icon name="arrow" size={14} /></button></div>
    </article>
  );
}

function ControlRoom({ overview, refresh, setView, sample, openCase }) {
  const [runNotice, setRunNotice] = useState("");
  async function runAction(action, runId) {
    try {
      const result = await request(`${API}/runs/${runId}/${action}`, { method: "POST" });
      setRunNotice(action === "stop" ? "تم طلب الإيقاف الآمن عند أقرب نقطة حفظ." : `تم استئناف التشغيل ${shortId(result.run_id)}.`);
      refresh();
    } catch (error) { setRunNotice(error.message); }
  }
  return (
    <>
      <RunLauncher overview={overview} onCreated={() => { refresh(); setTimeout(refresh, 1200); }} />
      {runNotice && <div className="inlineNotice">{runNotice}<button onClick={() => setRunNotice("")}><Icon name="close" size={14} /></button></div>}
      <div className="dualLayout">
        <RunPulse run={overview?.latest?.run} coverage={overview?.latest?.coverage} onRunAction={runAction} />
        <KnowledgePulse knowledge={overview?.knowledge} onOpen={() => setView("knowledge")} />
      </div>
      <SamplePreview items={sample} onOpen={openCase} onAll={() => setView("evidence")} />
    </>
  );
}

function EvidenceLab({ overview, openCase, refreshKey }) {
  const runs = overview?.runs || [];
  const [runId, setRunId] = useState(overview?.latest?.run?.id || "");
  const [category, setCategory] = useState("all");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => { if (!runId && overview?.latest?.run?.id) setRunId(overview.latest.run.id); }, [overview, runId]);
  useEffect(() => {
    if (!runId) { setItems([]); setLoading(false); return; }
    setLoading(true);
    request(`${API}/review-sample?run_id=${encodeURIComponent(runId)}&category=${category}&limit=20`)
      .then((data) => setItems(data.items)).finally(() => setLoading(false));
  }, [runId, category, refreshKey]);
  return (
    <section className="workspacePanel">
      <div className="labIntro">
        <div><span className="eyebrow dark"><Icon name="evidence" size={16} /> Evidence Lab</span><h2>راجع الحكم من الدليل،<br /><em>لا من الرقم.</em></h2><p>كل بطاقة توضح قاعدة الشركة المستخدمة، كلام العميل، رد الموظف، وسبب القرار.</p></div>
        <div className="runSelector"><label>التشغيل</label><select value={runId} onChange={(e) => setRunId(e.target.value)}>{runs.map((run) => <option key={run.id} value={run.id}>{shortId(run.id)} — {statusText[run.status]}</option>)}</select></div>
      </div>
      <div className="filterBar">
        {[["all", "بانتظار المراجعة"], ["non_compliant", "المخالفات"], ["compliant", "الملتزم"], ["needs_rule", "يحتاج قاعدة"], ["not_scorable", "خارج النطاق"], ["reviewed", "تمت مراجعتها"]].map(([id, label]) => <button key={id} className={category === id ? "active" : ""} onClick={() => setCategory(id)}>{label}</button>)}
        <span>{items.length} حالة</span>
      </div>
      {loading ? <div className="loadingState"><span className="spinner dark" /> جاري تجهيز العينة...</div> : <div className="evidenceGrid">{items.map((item) => <CaseCard key={item.session_id} item={item} onOpen={openCase} />)}{!items.length && <div className="emptyPanel">لا توجد حالات من هذا النوع في التشغيل المختار.</div>}</div>}
    </section>
  );
}

function KnowledgeLoop({ onChanged }) {
  const [data, setData] = useState({ items: [], candidates: [] });
  const [tab, setTab] = useState("candidates");
  const [search, setSearch] = useState("");
  const [drafts, setDrafts] = useState({});
  const [notice, setNotice] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const load = useCallback(() => request(`${API}/knowledge?${search ? `search=${encodeURIComponent(search)}` : ""}`).then(setData), [search]);
  useEffect(() => { const timer = setTimeout(load, 200); return () => clearTimeout(timer); }, [load]);
  async function analyzeDrafts() {
    const runIds = [...new Set(data.candidates.filter((item) => item.status === "pending" && item.analysis_status !== "complete").map((item) => item.run_id))];
    if (!runIds.length) { setNotice("كل المسودات الحالية خضعت بالفعل لفحص التكرار والتعارض."); return; }
    setAnalyzing(true); setNotice("الوكيل يقارن المسودات بقاعدة المعرفة...");
    try {
      let total = 0;
      for (const run_id of runIds) {
        const result = await request(`${API}/knowledge-candidates/analyze`, { method: "POST", body: JSON.stringify({ run_id }) });
        total += result.analyzed;
      }
      setNotice(`اكتمل فحص ${total} مسودة: صياغة، تكرار، وتعارض.`);
      await load(); onChanged();
    } catch (error) { setNotice(error.message); } finally { setAnalyzing(false); }
  }
  async function review(candidate, decision, resolution = "new") {
    try {
      await request(`${API}/knowledge-candidates/${candidate.id}/review`, { method: "POST", body: JSON.stringify({ decision, resolution, rule_text: drafts[candidate.id] ?? candidate.proposed_rule, reviewer: "Quality Manager" }) });
      setNotice(decision === "approved" ? resolution === "update" ? "تم تحديث القاعدة الموجودة دون إنشاء نسخة مكررة." : "تم اعتماد القاعدة الجديدة وأصبحت فعّالة." : resolution === "duplicate" ? "تم توثيق التكرار ولم تُضف قاعدة جديدة." : "تم استبعاد المسودة مع الاحتفاظ بسجل القرار.");
      load(); onChanged();
    } catch (error) { setNotice(error.message); }
  }
  const pending = data.candidates.filter((item) => item.status === "pending");
  return (
    <section className="workspacePanel knowledgeWorkspace">
      <div className="labIntro knowledgeIntro">
        <div><span className="eyebrow dark"><Icon name="knowledge" size={16} /> Knowledge Loop</span><h2>المحرك يقترح.<br /><em>المشرف يعتمد.</em></h2><p>بعد كل تشغيل تتحول الفجوات المتكررة إلى مسودات. لا تدخل أي مسودة في الحكم قبل موافقتك.</p></div>
        <div className="loopDiagram"><span>RUN</span><i>←</i><span>GAPS</span><i>←</i><strong>REVIEW</strong><i>←</i><span>KB</span></div>
      </div>
      {notice && <div className="notice">{notice}<button onClick={() => setNotice("")}><Icon name="close" size={15} /></button></div>}
      <div className="knowledgeToolbar">
        <div className="segmented"><button className={tab === "candidates" ? "active" : ""} onClick={() => setTab("candidates")}>مسودات تنتظر القرار <b>{pending.length}</b></button><button className={tab === "active" ? "active" : ""} onClick={() => setTab("active")}>المعرفة الفعّالة <b>{data.items.length}</b></button></div>
        {tab === "candidates" && <button className="aiAnalyze" onClick={analyzeDrafts} disabled={analyzing}><Icon name="spark" size={16} />{analyzing ? "جاري فحص KB..." : "فحص التكرار والتعارض بالـAI"}</button>}
        {tab === "active" && <div className="searchBox"><Icon name="search" size={17} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="ابحث في القواعد..." /></div>}
      </div>
      {tab === "candidates" ? <div className="candidateList">
        {pending.map((candidate) => <article className="candidate" key={candidate.id}>
          <div className="candidateIndex">{candidate.occurrence_count}<small>حالة</small></div>
          <div className="candidateBody"><div className="candidateMeta"><span className="overline">AUTO-DISCOVERED GAP · RUN {shortId(candidate.run_id)}</span><span className={`candidateKind ${candidate.candidate_kind}`}>{({ new: "قاعدة جديدة", duplicate: "تكرار", conflict: "تعارض", update: "تحديث مقترح", insufficient: "دليل غير كافٍ", unreviewed: "لم يفحصها AI" })[candidate.candidate_kind] || "لم يفحصها AI"}</span></div><h3>{candidate.title}</h3><p>{candidate.rationale}</p>
            {candidate.evidence?.length > 0 && <div className="evidenceStrip">{candidate.evidence.slice(0, 2).map((line, index) => <span key={index}>{line}</span>)}</div>}
            {candidate.analysis_status === "complete" && <div className="aiAssessment"><div><Icon name="spark" size={17} /><b>تحليل وكيل المعرفة</b></div><p>{candidate.ai_reason || "اكتملت المقارنة مع القواعد الأقرب."}</p>{candidate.similar_knowledge_id && <div className="similarRule"><span>أقرب قاعدة · تشابه {Math.round((candidate.similarity_score || 0) * 100)}%</span><b>#{candidate.similar_knowledge_id} — {candidate.similar_knowledge_title}</b><p>{candidate.similar_knowledge_content}</p></div>}{candidate.conflict_notes && <p className="conflictNote"><Icon name="alert" size={14} /> {candidate.conflict_notes}</p>}</div>}
            {candidate.analysis_status === "failed" && <div className="formMessage bad">تعذر فحص هذه المسودة بالـAI. يمكنك إعادة المحاولة أو مراجعتها يدويًا.</div>}
            <label>صياغة قاعدة الشركة المقترحة</label><textarea rows="3" value={drafts[candidate.id] ?? candidate.proposed_rule} onChange={(e) => setDrafts({ ...drafts, [candidate.id]: e.target.value })} placeholder="مثال: عند سؤال العميل عن السعر أو الشحن، يجب حساب الإجمالي مع الشحن..." />
            <div className="candidateActions">{candidate.candidate_kind === "duplicate" ? <button className="duplicateAction" onClick={() => review(candidate, "rejected", "duplicate")}><Icon name="check" size={17} /> تأكيد التكرار وعدم الإضافة</button> : <><button className="approve" onClick={() => review(candidate, "approved", "new")}><Icon name="check" size={17} /> اعتماد كقاعدة جديدة</button>{candidate.similar_knowledge_id && ["update", "conflict"].includes(candidate.candidate_kind) && <button className="updateAction" onClick={() => review(candidate, "approved", "update")}>تحديث القاعدة #{candidate.similar_knowledge_id}</button>}</>}<button className="reject" onClick={() => review(candidate, "rejected", "reject")}>ليست قاعدة مطلوبة</button></div>
          </div>
        </article>)}
        {!pending.length && <div className="emptyPanel successEmpty"><Icon name="check" /> لا توجد مسودات تنتظر المراجعة.</div>}
      </div> : <div className="rulesTable">{data.items.map((item) => <article key={item.id}><div><span className="ruleCategory">{item.category}</span><h4>{item.title}</h4><p>{item.content}</p></div><time>{formatDate(item.updated_at)}</time></article>)}</div>}
    </section>
  );
}

function RunsHistory({ overview }) {
  const runs = overview?.runs || [];
  return (
    <section className="workspacePanel">
      <div className="labIntro"><div><span className="eyebrow dark"><Icon name="runs" size={16} /> Run history</span><h2>كل تشغيل،<br /><em>له أثر واضح.</em></h2><p>الحجم، التغطية، التكلفة والحالة من مكان واحد.</p></div></div>
      <div className="runsTable"><div className="tableHead"><span>التشغيل</span><span>الحالة</span><span>المعالجة</span><span>يحتاج قاعدة</span><span>التكلفة</span><span>آخر تحديث</span></div>{runs.map((run) => <div className="tableRow" key={run.id}><strong>{shortId(run.id)}</strong><span><i className={`stateDot ${run.status}`} />{statusText[run.status]}</span><span>{run.processed_count} / {run.total_count || "—"}</span><span>{run.clarification_count}</span><span>{money(run.cost_usd)}</span><time>{formatDate(run.updated_at)}</time></div>)}</div>
    </section>
  );
}

function CaseDrawer({ item, close, afterReview, goKnowledge }) {
  const [mode, setMode] = useState("inspect");
  const currentVerdict = item.category === "compliant" ? "compliant" : "non_compliant";
  const [verdict, setVerdict] = useState(currentVerdict);
  const [reason, setReason] = useState("");
  const [rule, setRule] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const meta = categoryMeta[item.category] || categoryMeta.not_scorable;
  const isCorrection = verdict !== currentVerdict;
  async function submit() {
    setSaving(true); setError("");
    try {
      const result = await request(`${API}/cases/${item.run_id}/${item.session_id}/review`, { method: "POST", body: JSON.stringify({ manager_verdict: verdict, decision: isCorrection ? "corrected" : "confirmed", manager_reason: reason, knowledge_rule: isCorrection ? rule : "" }) });
      afterReview(result); close();
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  return <div className="drawerBackdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}><aside className="caseDrawer">
    <div className="drawerHead"><div><span className={`verdict ${meta.tone}`}>{meta.label}</span><h3>الحالة {shortId(item.session_id)}</h3></div><button className="iconButton" onClick={close}><Icon name="close" /></button></div>
    <div className="reasonBanner"><Icon name={item.category === "non_compliant" ? "alert" : "spark"} /><div><small>سبب القرار</small><p>{item.explanation}</p></div></div>
    {item.reviewed && <div className="reviewedSummary"><Icon name="check" /><div><b>راجعها {item.reviewer || "المشرف"}</b><p>{item.manager_reason}</p></div></div>}
    <div className="drawerSection"><span className="sectionLabel">الدليل من المحادثة</span>{(item.evidence || []).map((line, index) => <div className={`messageEvidence ${index === 0 ? "customer" : "agent"}`} key={index}><small>{index === 0 ? "العميل" : "الموظف"}</small><p>{line}</p></div>)}</div>
    <div className="drawerSection"><span className="sectionLabel">القاعدة التي بُني عليها الحكم</span><div className="referenceBox">{item.reference || "لم توجد قاعدة مطابقة — لذلك لم تصدر مخالفة."}</div></div>
    <div className="signalRow"><span>ثقة النموذج <b>{item.confidence != null ? `${Math.round(item.confidence * 100)}%` : "—"}</b></span><span>الموظف <b>{item.agent_name}</b></span></div>
    {item.status === "scored" ? mode === "inspect" ? <div className="drawerActions"><button className="primaryAction" onClick={() => setMode("review")}><Icon name="check" /> {item.reviewed ? "تعديل مراجعة المشرف" : "راجع وصحح الحكم"}</button></div> : <div className="reviewForm"><h4>قرار المشرف</h4><div className="verdictChoice"><button className={verdict === "compliant" ? "selected good" : ""} onClick={() => setVerdict("compliant")}>ملتزم</button><button className={verdict === "non_compliant" ? "selected bad" : ""} onClick={() => setVerdict("non_compliant")}>مخالفة</button></div><label>لماذا؟</label><textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="اكتب سببًا واضحًا يمكن مراجعته لاحقًا" />{isCorrection ? <><label>القاعدة الجديدة القابلة لإعادة الاستخدام</label><textarea value={rule} onChange={(e) => setRule(e.target.value)} placeholder="اكتب قاعدة الشركة التي تثبت سبب التصحيح للحالات المشابهة" /><p className="learningHint">سيُضاف التصحيح إلى KB بعد الحفظ.</p></> : <p className="noDuplicateHint"><Icon name="check" size={15} /> سيتم تسجيل التأكيد فقط، ولن ننسخ القاعدة الموجودة داخل KB.</p>}{error && <div className="formMessage bad">{error}</div>}<button className="primaryAction" onClick={submit} disabled={saving || !reason.trim() || (isCorrection && !rule.trim())}>{saving ? "جاري الحفظ..." : isCorrection ? "حفظ التصحيح وتعليم المحرك" : "تأكيد الحكم وحفظ المراجعة"}</button></div> : item.category === "needs_rule" && <div className="drawerActions"><button className="primaryAction" onClick={() => { close(); goKnowledge(); }}>انتقل لمسودات المعرفة</button></div>}
  </aside></div>;
}

function App() {
  const [theme, setTheme] = useState(() => {
    try {
      const saved = localStorage.getItem("cxquality-theme");
      if (saved === "light" || saved === "dark") return saved;
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch {
      return "light";
    }
  });
  const [auth, setAuth] = useState({ state: "checking", username: "" });
  const [view, setView] = useState("home");
  const [menu, setMenu] = useState(false);
  const [overview, setOverview] = useState(null);
  const [sample, setSample] = useState([]);
  const [selectedCase, setSelectedCase] = useState(null);
  const [error, setError] = useState("");
  const [reviewRevision, setReviewRevision] = useState(0);
  const [toast, setToast] = useState("");
  const load = useCallback(async () => {
    try {
      const next = await request(`${API}/overview`);
      setOverview(next); setError("");
      if (next.latest?.run?.id) {
        const review = await request(`${API}/review-sample?run_id=${next.latest.run.id}&limit=6`);
        setSample(review.items);
      }
    } catch (err) { setError(err.message); }
  }, []);
  useEffect(() => {
    request(`${API}/auth/status`)
      .then((result) => setAuth({ state: result.authenticated ? "authenticated" : "anonymous", username: result.username || "" }))
      .catch(() => setAuth({ state: "anonymous", username: "" }));
  }, []);
  useEffect(() => {
    const unauthorized = () => setAuth({ state: "anonymous", username: "" });
    window.addEventListener("cxquality:unauthorized", unauthorized);
    return () => window.removeEventListener("cxquality:unauthorized", unauthorized);
  }, []);
  useEffect(() => { if (auth.state === "authenticated") load(); }, [auth.state, load]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    try { localStorage.setItem("cxquality-theme", theme); } catch { /* storage is optional */ }
  }, [theme]);
  useEffect(() => {
    if (!overview?.control?.active_runs) return;
    const timer = setInterval(load, 5000); return () => clearInterval(timer);
  }, [overview?.control?.active_runs, load]);
  const headings = useMemo(() => ({ home: ["صباح الجودة", "قرارات الشركة أولًا — والأرقام تأتي بعدها."], evidence: ["مختبر الأدلة", "اختبر الحكم، صححه، واجعل التصحيح قاعدة."], knowledge: ["دورة المعرفة", "ذاكرة الشركة تتطور تحت إشرافك."], runs: ["سجل التشغيل", "رؤية واضحة لكل تحليل تم تنفيذه."] }), []);
  const [title, subtitle] = headings[view];
  const reviewCompleted = (result) => {
    setReviewRevision((value) => value + 1);
    setToast(result.learned ? "تم حفظ التصحيح وإضافة القاعدة الجديدة إلى KB." : "تم تأكيد الحكم وحفظ المراجعة دون تكرار القاعدة في KB.");
    load();
  };
  const toggleTheme = () => setTheme((current) => current === "dark" ? "light" : "dark");
  const logout = async () => {
    try { await request(`${API}/auth/logout`, { method: "POST", body: "{}" }); } catch { /* clear locally */ }
    setOverview(null); setSample([]); setSelectedCase(null); setAuth({ state: "anonymous", username: "" });
  };
  if (auth.state === "checking") return <main className="authLoading" dir="rtl"><span className="spinner dark" /> جاري التحقق من صلاحية الدخول...</main>;
  if (auth.state !== "authenticated") return <LoginScreen theme={theme} toggleTheme={toggleTheme} onLogin={(result) => setAuth({ state: "authenticated", username: result.username || "admin" })} />;
  return <div className="appShell">
    <Navigation active={view} setActive={setView} open={menu} setOpen={setMenu} />
    {menu && <div className="navBackdrop" onClick={() => setMenu(false)} />}
    <main><Header title={title} subtitle={subtitle} setMenu={setMenu} theme={theme} toggleTheme={toggleTheme} username={auth.username} onLogout={logout} />{error && <div className="globalError">{error}</div>}{toast && <div className="successToast"><Icon name="check" />{toast}<button onClick={() => setToast("")}><Icon name="close" size={14} /></button></div>}<div className="content">
      {view === "home" && <ControlRoom overview={overview} refresh={load} setView={setView} sample={sample} openCase={setSelectedCase} />}
      {view === "evidence" && <EvidenceLab overview={overview} openCase={setSelectedCase} refreshKey={reviewRevision} />}
      {view === "knowledge" && <KnowledgeLoop onChanged={load} />}
      {view === "runs" && <RunsHistory overview={overview} />}
    </div></main>
    {selectedCase && <CaseDrawer item={selectedCase} close={() => setSelectedCase(null)} afterReview={reviewCompleted} goKnowledge={() => setView("knowledge")} />}
  </div>;
}

createRoot(document.getElementById("root")).render(<App />);
