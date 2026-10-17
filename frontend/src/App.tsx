import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertCircle, AlertTriangle, ArrowRight, Bot, Check,
  CheckCircle2, ChevronDown, Clock3, Cloud, Cpu, CreditCard, Database, FileText, Gauge, Layers3, ListFilter,
  MapPin, Minus, PackageCheck, Plus, Radio, RefreshCw, Search, Settings2, Shield, ShieldCheck, ShoppingBag,
  ShoppingCart, Sparkles, Workflow, X, XCircle,
} from 'lucide-react'
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000/api'
type Order = { id: string; product: string; customer_ref: string; quantity: number; status: string; stage: string;
  elapsed_ms: number; retries: number; failure_category?: string; recovered: number; created_at: string }
type Metrics = { total_orders: number; completed: number; failed: number; delayed: number; manual_review: number;
  recovered: number; active_incidents: number; sla_breach_rate: number; average_elapsed_ms: number;
  failures_by_module: Record<string, number>; status_distribution: Record<string, number> }
type Incident = { id: string; category: string; module: string; orders: string[]; events: { order_id: string;
  http_status: number; message: string; created_at: string }[]; severity: string; probable_shared_cause: string;
  causation_confirmed: boolean; status: string }
type Action = { id: string; order_id: string; action: string; risk: string; requires_approval: boolean;
  status: string; evidence: Record<string, unknown>; created_at: string; result?: string }
type Page = 'Dashboard' | 'E-commerce Store' | 'Live Order Monitor' | 'Order Details' | 'AI Root-Cause Analysis' |
  'Incident Management' | 'Resolution Center' | 'AI vs Rule-Based Analysis' | 'Model Evaluation' | 'Settings'
type StoreProduct = { sku: string; name: string; description: string; category: string; price_cents: number; stock: number; color: string }
type StoreTracking = { order_id: string; status: string; stage: string; events: any[]; total_cents: number } | null
type StoreOrderSummary = { order_id: string; product_name: string; quantity: number; total_cents: number;
  status: string; created_at: string; stage: string; retries: number }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.status === 204 ? undefined as T : response.json()
}

const navItems: { label: Page; icon: typeof Activity; group: string }[] = [
  { label: 'Dashboard', icon: Gauge, group: 'MONITOR' },
  { label: 'E-commerce Store', icon: ShoppingCart, group: 'STORE' },
  { label: 'Live Order Monitor', icon: Workflow, group: 'MONITOR' },
  { label: 'Order Details', icon: PackageCheck, group: 'MONITOR' },
  { label: 'AI Root-Cause Analysis', icon: Sparkles, group: 'INTELLIGENCE' },
  { label: 'Incident Management', icon: AlertTriangle, group: 'INTELLIGENCE' },
  { label: 'Resolution Center', icon: ShieldCheck, group: 'INTELLIGENCE' },
  { label: 'AI vs Rule-Based Analysis', icon: Layers3, group: 'INTELLIGENCE' },
  { label: 'Model Evaluation', icon: Cpu, group: 'SYSTEM' },
  { label: 'Settings', icon: Settings2, group: 'SYSTEM' },
]

const colors = ['#16a477', '#6e88e6', '#f0a43d', '#e56e72', '#9a74d2', '#41a8b7']
const faultOptions = [
  ['503', 'HTTP 503 service unavailable'], ['500', 'HTTP 500 internal error'], ['504', 'HTTP 504 timeout'],
  ['429', 'HTTP 429 rate limit'], ['auth', 'Authentication failure'], ['malformed', 'Malformed response'],
  ['invalid_address', 'Invalid delivery address'], ['inventory_unavailable', 'Inventory rejection'],
  ['payment_rejected', 'Payment rejection'], ['queue_backlog', 'Queue backlog'],
  ['database_bottleneck', 'Database bottleneck'], ['recovery_fails', 'Retry exhaustion'],
  ['unfamiliar', 'Unfamiliar incident (uncertain)'],
]
const stages = ['Order Management', 'Payment', 'Inventory', 'Delivery', 'Notification']

export default function App() {
  const [page, setPage] = useState<Page>('Dashboard')
  const [orders, setOrders] = useState<Order[]>([])
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [actions, setActions] = useState<Action[]>([])
  const [evaluation, setEvaluation] = useState<any>(null)
  const [settings, setSettings] = useState<any>(null)
  const [health, setHealth] = useState(false)
  const [streamLive, setStreamLive] = useState(false)
  const [storeTracking, setStoreTracking] = useState<StoreTracking>(null)
  const [selected, setSelected] = useState('')
  const [analysis, setAnalysis] = useState<any>(null)
  const [explanation, setExplanation] = useState<any>(null)
  const [comparison, setComparison] = useState<any>(null)
  const [detail, setDetail] = useState<any>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [fault, setFault] = useState('')
  const [faultModule, setFaultModule] = useState('Inventory')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [m, o, i, a, ev, s, h] = await Promise.all([
        request<Metrics>('/dashboard'), request<Order[]>('/orders?limit=300'), request<Incident[]>('/incidents'),
        request<Action[]>('/actions'), request('/evaluation'), request('/settings'), request<any>('/health'),
      ])
      setMetrics(m); setOrders(o); setIncidents(i); setActions(a); setEvaluation(ev); setSettings(s); setHealth(h.status === 'ok')
      setError('')
      if (o.length) setSelected(current => current || o[0].id)
    } catch (e) { setHealth(false); setError(e instanceof Error ? e.message : 'Could not connect to API') }
  }, [])

  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 12000); return () => window.clearInterval(timer) }, [refresh])
  useEffect(() => {
    const stream = new EventSource(`${API}/stream`)
    stream.onopen = () => setStreamLive(true)
    stream.onerror = () => setStreamLive(false)
    stream.onmessage = event => {
      try {
        const update = JSON.parse(event.data)
        if (update.type === 'order_event') {
          void refresh()
          setStoreTracking(current => {
            if (!current || current.order_id !== update.order_id) return current
            return { ...current, status: update.order.status, stage: update.order.stage,
              events: [...current.events, update] }
          })
        }
      } catch { flash('Received an unreadable live event from the order service.') }
    }
    return () => stream.close()
  }, [refresh])
  useEffect(() => {
    if (selected) request<any>(`/orders/${selected}`).then(setDetail).catch(() => setDetail(null))
  }, [selected, orders])

  const flash = (text: string) => { setToast(text); window.setTimeout(() => setToast(''), 3200) }
  const run = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true)
    try { await fn(); await refresh(); flash(success) }
    catch (e) { flash(e instanceof Error ? e.message : 'Action failed') }
    finally { setBusy(false) }
  }
  const createOrder = () => run(async () => {
    const created = await request<Order>('/orders', { method: 'POST', body: JSON.stringify({
      product: ['Ceramic Pour-Over Set', 'Wireless Headphones', 'Travel Backpack'][Math.floor(Math.random() * 3)],
      quantity: 1 + Math.floor(Math.random() * 3), fault: fault || null, fault_module: faultModule, process: true,
    }) })
    setSelected(created.id)
  }, fault ? 'Fault scenario processed' : 'Order processed successfully')
  const loadScenarios = () => run(() => request('/scenarios/load', { method: 'POST' }), 'Synthetic scenario set loaded')
  const analyze = async (compareMode = false) => {
    if (!selected) return
    setBusy(true)
    try {
      const path = compareMode ? '/analysis/compare' : '/analysis'
      const result = await request<any>(path, { method: 'POST', body: JSON.stringify({ order_id: selected }) })
      if (compareMode) setComparison(result); else { setAnalysis(result); setExplanation(null) }
      setPage(compareMode ? 'AI vs Rule-Based Analysis' : 'AI Root-Cause Analysis')
    } catch (e) { flash(e instanceof Error ? e.message : 'Analysis failed') }
    finally { setBusy(false) }
  }
  const explain = async () => {
    if (!selected) return
    setBusy(true)
    try {
      setExplanation(await request<any>('/analysis/explain', { method: 'POST', body: JSON.stringify({ order_id: selected }) }))
    } catch (e) { flash(e instanceof Error ? e.message : 'Could not generate explanation') }
    finally { setBusy(false) }
  }
  const filtered = useMemo(() => orders.filter(o => {
    const q = search.toLowerCase()
    return (!q || [o.id, o.product, o.customer_ref].some(s => s.toLowerCase().includes(q))) &&
      (!statusFilter || o.status === statusFilter)
  }), [orders, search, statusFilter])
  const gotoOrder = (id: string, destination: Page = 'Order Details') => { setSelected(id); setPage(destination) }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Activity size={21}/></div><div><strong>OrderFlow</strong><span> SENTINEL</span></div></div>
      <div className="workspace-label">WORKSPACE</div>
      {['MONITOR', 'STORE', 'INTELLIGENCE', 'SYSTEM'].map(group => <div className="nav-group" key={group}>
        <div className="nav-heading">{group}</div>
        {navItems.filter(n => n.group === group).map(({ label, icon: Icon }) =>
          <button key={label} className={`nav-item ${page === label ? 'active' : ''}`} onClick={() => setPage(label)}>
            <Icon size={17}/><span>{label}</span>{label === 'Incident Management' && !!incidents.length &&
              <em className="nav-count">{incidents.length}</em>}
          </button>)}
      </div>)}
      <div className="sidebar-bottom"><div className="system-card"><div className="sys-top"><span className={`live-dot ${health ? '' : 'offline'}`}/><b>{health ? 'System operational' : 'API disconnected'}</b></div>
        <span>{health ? 'Local ML models active' : 'Start the FastAPI backend'}</span><div className="sys-progress"><i/></div></div>
        <div className="user"><div className="avatar">OP</div><div><b>Operations</b><span>Workspace admin</span></div><ChevronDown size={15}/></div>
      </div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="crumb">Operations <span>/</span> <strong>{page}</strong></div>
        <div className="top-actions"><div className="environment"><span className={`live-dot ${streamLive ? '' : 'offline'}`}/>{streamLive ? 'LIVE EVENTS' : 'RECONNECTING'}</div>
          <button className="icon-button" aria-label="Refresh data" onClick={() => refresh()}><RefreshCw size={17}/></button>
          <div className="avatar small">OP</div></div>
      </header>
      <div className="page-content">
        {error && <div className="banner error-banner"><AlertCircle size={17}/><div><b>Backend unavailable</b><span>{error}. Start the API at <code>127.0.0.1:8000</code>.</span></div></div>}
        {page === 'Dashboard' && <Dashboard metrics={metrics} orders={orders} incidents={incidents} onOpen={gotoOrder}
          onCreate={createOrder} busy={busy} onScenarios={loadScenarios} onMonitor={() => setPage('Live Order Monitor')}/>}
        {page === 'E-commerce Store' && <Storefront onOrderCreated={async (tracking: StoreTracking) => {
          setStoreTracking(tracking); await refresh()
        }} tracking={storeTracking} onViewOrder={(id: string) => gotoOrder(id)} flash={flash}/>}
        {page === 'Live Order Monitor' && <OrdersPage orders={filtered} search={search} setSearch={setSearch} statusFilter={statusFilter}
          setStatusFilter={setStatusFilter} onOpen={gotoOrder} onCreate={createOrder} busy={busy} fault={fault} setFault={setFault}
          faultModule={faultModule} setFaultModule={setFaultModule}/>}
        {page === 'Order Details' && <OrderDetails order={detail} onAnalyze={() => analyze(false)} busy={busy} onInject={(f: string, m: string) =>
          run(() => request(`/orders/${selected}/inject`, { method: 'POST', body: JSON.stringify({ fault: f, module: m }) }), 'Fault injected and pipeline executed')}/>}
        {page === 'AI Root-Cause Analysis' && <AnalysisPage result={analysis} selected={selected} orders={orders}
          setSelected={setSelected} onAnalyze={() => analyze(false)} onExplain={explain} explanation={explanation} busy={busy} />}
        {page === 'Incident Management' && <IncidentsPage incidents={incidents} onOpen={gotoOrder}/>}
        {page === 'Resolution Center' && <ResolutionPage actions={actions} onDecision={(id: string, approve: boolean) =>
          run(() => request(`/actions/${id}/decision`, { method: 'POST', body: JSON.stringify({ approve, actor: 'Operations' }) }),
            approve ? 'Approval recorded; safety policy enforced' : 'Action declined')}/>}
        {page === 'AI vs Rule-Based Analysis' && <ComparePage result={comparison} orders={orders} selected={selected}
          setSelected={setSelected} onCompare={() => analyze(true)} busy={busy}/>}
        {page === 'Model Evaluation' && <EvaluationPage evaluation={evaluation}/>}
        {page === 'Settings' && <SettingsPage settings={settings} onSave={async (payload: Record<string, unknown>) =>
          run(() => request('/settings', { method: 'PUT', body: JSON.stringify(payload) }), 'Settings saved')}/>}
      </div>
    </main>
    {toast && <div className="toast"><CheckCircle2 size={18}/>{toast}<button onClick={() => setToast('')}><XCircle size={15}/></button></div>}
  </div>
}

function Heading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string;
  action?: React.ReactNode }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{action}</div>
}
function Button({ children, onClick, kind = 'primary', disabled = false }: { children: React.ReactNode;
  onClick?: () => void; kind?: string; disabled?: boolean }) {
  return <button disabled={disabled} onClick={onClick} className={`button ${kind}`}>{children}</button>
}
function Status({ value }: { value: string }) {
  const style = value === 'COMPLETED' || value === 'REVIEWED' ? 'green' : value === 'FAILED' || value === 'MANUAL_REVIEW_REQUIRED' ? 'red' :
    value === 'DELAYED' || value.includes('PENDING') ? 'amber' : value === 'RECOMMENDED' ? 'blue' : 'gray'
  return <span className={`status ${style}`}><i/>{value.replaceAll('_', ' ')}</span>
}
function CardTitle({ title, meta, icon }: { title: string; meta?: string; icon?: React.ReactNode }) {
  return <div className="card-title">{icon && <span className="card-title-icon">{icon}</span>}<div><h3>{title}</h3>{meta && <span>{meta}</span>}</div></div>
}
function Dashboard({ metrics, orders, incidents, onOpen, onCreate, busy, onScenarios, onMonitor }: any) {
  const statusData = metrics ? Object.entries(metrics.status_distribution || {}).map(([name, value]) => ({ name: name.replaceAll('_', ' '), value })) : []
  const failureData = metrics ? Object.entries(metrics.failures_by_module || {}).map(([name, errors]) => ({ name, errors })) : []
  const trend = orders.slice(0, 8).reverse().map((o: Order) => ({ name: o.id.slice(-5), ms: o.elapsed_ms }))
  return <>
    <Heading eyebrow="OVERVIEW" title="Operations dashboard" description="A live view of order health, service signals, and safe recovery activity."
      action={<div className="head-buttons"><Button kind="secondary" onClick={onScenarios}><Sparkles size={16}/> Load demo scenarios</Button><Button onClick={onCreate} disabled={busy}><ShoppingBag size={16}/> Create order</Button></div>}/>
    <div className="notice"><ShieldCheck size={17}/><span><b>Explainable local ML</b> — predictions are hypotheses backed by logged evidence. Recovery policies remain deterministic.</span><span className="notice-tag">NO PAID API REQUIRED</span></div>
    <div className="metric-grid">
      <Metric icon={<ShoppingBag/>} label="Total orders" value={metrics?.total_orders ?? '—'} note="Across all processing stages" tint="blue"/>
      <Metric icon={<CheckCircle2/>} label="Completed" value={metrics?.completed ?? '—'} note={`${metrics?.recovered ?? 0} safely recovered`} tint="green"/>
      <Metric icon={<AlertCircle/>} label="Failed orders" value={metrics?.failed ?? 0} note={`${metrics?.manual_review ?? 0} need operator review`} tint="red"/>
      <Metric icon={<Clock3/>} label="SLA risk" value={`${Math.round((metrics?.sla_breach_rate || 0) * 100)}%`} note={`${metrics?.delayed ?? 0} delayed`} tint="amber"/>
    </div>
    <div className="chart-grid">
      <section className="card chart-card"><CardTitle title="Order status distribution" meta="Live snapshot" icon={<Activity size={17}/>}
        /><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={statusData} dataKey="value" nameKey="name" innerRadius={67} outerRadius={95} paddingAngle={3}>
          {statusData.map((_: any, i: number) => <Cell key={i} fill={colors[i % colors.length]}/>)}</Pie><Tooltip/><Legend verticalAlign="middle" align="right" layout="vertical"/></PieChart></ResponsiveContainer>
        {statusData.length === 0 && <div className="empty-chart">No order data yet</div>}</div></section>
      <section className="card chart-card"><CardTitle title="Failures by module" meta="Observed API errors" icon={<Layers3 size={17}/>}
        /><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><BarChart data={failureData} margin={{ top: 12, right: 10, bottom: 2, left: -18 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f5"/><XAxis dataKey="name" tick={{ fontSize: 11 }} axisLine={false} tickLine={false}/>
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} axisLine={false} tickLine={false}/><Tooltip/><Bar dataKey="errors" fill="#5275db" radius={[5, 5, 0, 0]} barSize={34}/></BarChart></ResponsiveContainer>
        {failureData.length === 0 && <div className="empty-chart">No failures observed</div>}</div></section>
      <section className="card chart-card"><CardTitle title="Recent processing time" meta="Elapsed time per order" icon={<Clock3 size={17}/>}
        /><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><LineChart data={trend} margin={{ top: 12, right: 12, bottom: 2, left: -12 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f5"/><XAxis dataKey="name" tick={{ fontSize: 11 }} axisLine={false} tickLine={false}/>
          <YAxis tick={{ fontSize: 11 }} axisLine={false} tickLine={false}/><Tooltip/><Line type="monotone" dataKey="ms" stroke="#1b9b79" strokeWidth={2.5} dot={{ r: 3, fill: '#1b9b79' }}/></LineChart></ResponsiveContainer>
        {trend.length === 0 && <div className="empty-chart">No processing activity yet</div>}</div></section>
      <section className="card incident-card"><CardTitle title="Active incidents" meta={`${incidents.length} correlated groups`} icon={<AlertTriangle size={17}/>}
        /><div className="incident-list">{incidents.slice(0, 4).map((i: Incident) => <div className="incident-row" key={i.id}>
          <span className={`severity-mark ${i.severity.toLowerCase()}`}/><div className="incident-copy"><b>{i.category.replaceAll('_', ' ')} · {i.module}</b>
            <span>{i.orders.length} affected order{i.orders.length === 1 ? '' : 's'} · {i.probable_shared_cause}</span></div><ArrowRight size={15}/></div>)}
          {incidents.length === 0 && <div className="empty-state"><Shield size={22}/>No active incidents detected</div>}</div></section>
    </div>
    <section className="card table-card"><div className="table-head"><CardTitle title="Recent orders" meta="Latest order pipeline activity" icon={<Workflow size={17}/>}
      /><button className="text-button" onClick={onMonitor}>View order monitor <ArrowRight size={15}/></button></div>
      <OrderTable orders={orders.slice(0, 6)} onOpen={onOpen}/></section>
  </>
}
function Metric({ icon, label, value, note, tint }: any) {
  return <div className="metric-card"><div className={`metric-icon ${tint}`}>{icon}</div><span className="metric-label">{label}</span>
    <strong>{value}</strong><span className="metric-note">{note}</span></div>
}
function OrderTable({ orders, onOpen }: { orders: Order[]; onOpen: (id: string) => void }) {
  return <div className="table-scroll"><table><thead><tr><th>ORDER</th><th>PRODUCT</th><th>CURRENT STAGE</th><th>STATUS</th><th>ELAPSED</th><th>RETRIES</th><th></th></tr></thead>
    <tbody>{orders.map(o => <tr key={o.id} onClick={() => onOpen(o.id)}><td><b className="order-id">{o.id}</b><span className="subcell">{o.customer_ref}</span></td>
      <td>{o.product.replace('Demo · ', '')}</td><td><span className="stage-cell"><span className="stage-dot"/>{o.stage}</span></td>
      <td><Status value={o.status}/></td><td>{(o.elapsed_ms / 1000).toFixed(2)}s</td><td>{o.retries}</td><td><button className="row-open" aria-label="Open order"><ArrowRight size={16}/></button></td></tr>)}
      {orders.length === 0 && <tr><td colSpan={7} className="empty-row">No orders match. Create an order or load demo scenarios.</td></tr>}</tbody></table></div>
}
function OrdersPage({ orders, search, setSearch, statusFilter, setStatusFilter, onOpen, onCreate, busy,
  fault, setFault, faultModule, setFaultModule }: any) {
  return <><Heading eyebrow="ORDER OPERATIONS" title="Live order monitor" description="Search, filter, and inspect the complete five-stage order journey."
    action={<Button onClick={onCreate} disabled={busy}><ShoppingBag size={16}/> Create & process</Button>}/>
    <section className="card table-card"><div className="filterbar"><label className="searchbox"><Search size={16}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search order, product, customer"/></label>
      <label className="selectbox"><ListFilter size={16}/><select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
        <option value="">All statuses</option>{['COMPLETED','FAILED','DELAYED','MANUAL_REVIEW_REQUIRED','CREATED'].map(s => <option key={s}>{s}</option>)}</select></label>
      <div className="fault-controls"><span>Inject fault</span><select value={fault} onChange={e => setFault(e.target.value)}><option value="">No fault · normal order</option>{faultOptions.map(([v, label]) => <option value={v} key={v}>{label}</option>)}</select>
        {fault && <select value={faultModule} onChange={e => setFaultModule(e.target.value)}>{stages.map(s => <option key={s}>{s}</option>)}</select>}</div></div>
      <OrderTable orders={orders} onOpen={onOpen}/><div className="table-foot">Showing <b>{orders.length}</b> matching orders · Refreshes every 12 seconds</div></section></>
}
function OrderDetails({ order, onAnalyze, busy, onInject }: any) {
  const [module, setModule] = useState('Inventory')
  const [fault, setFault] = useState('503')
  if (!order) return <><Heading eyebrow="ORDER OPERATIONS" title="Order details" description="Select an order from the Live Order Monitor to see its full event timeline."/><div className="card empty-state large"><PackageCheck size={28}/>No order selected</div></>
  const events = order.events || []
  const failure = events.find((e: any) => e.event_type === 'ERROR')
  return <><Heading eyebrow="ORDER OPERATIONS" title="Order details" description="Full processing timeline, API responses, and recovery evidence."
    action={<Button onClick={onAnalyze} disabled={busy}><Sparkles size={16}/> Analyze with AI</Button>}/>
    <div className="order-summary"><div><span>ORDER ID</span><b>{order.id}</b></div><div><span>STATUS</span><Status value={order.status}/></div>
      <div><span>PRODUCT</span><b>{order.product}</b></div><div><span>ELAPSED</span><b>{(order.elapsed_ms / 1000).toFixed(2)} sec</b></div><div><span>RETRIES</span><b>{order.retries}</b></div></div>
    <div className="card journey-card"><CardTitle title="Order processing journey" meta="Order Management → Payment → Inventory → Delivery → Notification"/>
      <div className="journey">{stages.map((s, i) => { const evt = events.find((e: any) => e.stage === s && e.event_type === 'ERROR') ||
        events.find((e: any) => e.stage === s); const isFailed = evt?.event_type === 'ERROR';
        const done = events.some((e: any) => e.stage === s && (e.event_type === 'INFO' || e.event_type === 'RECOVERY'));
        return <div className={`journey-stage ${isFailed ? 'failed' : done ? 'done' : ''}`} key={s}><div className="journey-line"/>
          <div className="journey-node">{isFailed ? <AlertCircle size={18}/> : done ? <Check size={18}/> : <span>{i + 1}</span>}</div>
          <b>{s}</b><span>{isFailed ? `HTTP ${evt.response_code}` : done ? 'Completed' : 'Not reached'}</span></div>})}</div>
    </div>
    <div className="details-grid"><section className="card"><CardTitle title="Event timeline" meta={`${events.length} recorded events`} icon={<FileText size={17}/>}/>
      <div className="timeline">{events.map((e: any) => <div className={`timeline-item ${e.event_type === 'ERROR' ? 'event-error' : e.event_type === 'RECOVERY' ? 'event-recovery' : ''}`} key={e.id}>
        <span className="timeline-pin"/><div className="timeline-content"><div className="timeline-top"><b>{e.stage}</b><span>{new Date(e.created_at).toLocaleTimeString()}</span></div>
          <p>{e.message}</p>{e.response_code && <div className="event-meta"><span>HTTP {e.response_code}</span><span>{e.response_ms} ms</span></div>}</div></div>)}
        {events.length === 0 && <div className="empty-state">No timeline events yet</div>}</div></section>
      <section className="card"><CardTitle title="Fault injection" meta="Simulated local service only" icon={<AlertTriangle size={17}/>}/>
        <p className="muted-copy">Replay the pipeline with a selected fault to observe deterministic detection and recovery policy.</p>
        <label className="field-label">Fault scenario</label><select className="field" value={fault} onChange={e => setFault(e.target.value)}>{faultOptions.map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select>
        <label className="field-label">Affected stage</label><select className="field" value={module} onChange={e => setModule(e.target.value)}>{stages.map(s => <option key={s}>{s}</option>)}</select>
        <Button onClick={() => onInject(fault,module)}><AlertTriangle size={15}/> Inject fault & replay</Button>
        {failure && <div className="evidence-box"><b>Observed failure</b><span>HTTP {failure.response_code} · {failure.stage}</span><span>{failure.message}</span></div>}
      </section></div></>
}
function AnalysisPage({ result, selected, orders, setSelected, onAnalyze, onExplain, explanation, busy }: any) {
  return <><Heading eyebrow="LOCAL ML · EVIDENCE FIRST" title="AI root-cause analysis" description="Model predictions are separated from observed facts; low evidence returns an explicit unknown."
    action={<Button onClick={onAnalyze} disabled={!selected || busy}><Sparkles size={16}/> Run analysis</Button>}/>
    <div className="analysis-select card"><label>Analyze order</label><select value={selected} onChange={e => setSelected(e.target.value)}>
      {orders.map((o: Order) => <option key={o.id} value={o.id}>{o.id} · {o.product} · {o.status}</option>)}</select><span className="model-chip"><Cpu size={14}/> Local RandomForest</span></div>
    {!result || result.order.id !== selected ? <div className="card empty-state large"><Bot size={30}/><b>Ready to investigate</b><span>Choose an order and run the local root-cause model.</span></div> : <>
      <div className="analysis-grid"><section className="card prediction-card"><div className="prediction-header"><div className="ai-badge"><Sparkles size={17}/></div>
        <div><span className="eyebrow">PROBABLE ROOT CAUSE · NOT CONFIRMED</span><h2>{result.root_cause.prediction}</h2></div></div>
        <div className="confidence-row"><span>Model confidence</span><b>{Math.round(result.root_cause.confidence * 100)}%</b></div>
        <div className="confidence-track"><i style={{ width: `${result.root_cause.confidence * 100}%` }}/></div>
        <p className="analysis-explainer">{result.root_cause.explanation}</p>
        <div className="model-note"><Shield size={15}/> {result.root_cause.model} · prediction is not causation</div></section>
        <section className="card"><CardTitle title="Rule-based detection" meta="Conventional monitoring" icon={<ListFilter size={17}/>}/>
          <div className="rule-result"><span className={result.rule_based.detected ? 'rule-alert' : 'rule-ok'}>{result.rule_based.detected ? <AlertCircle/> : <CheckCircle2/>}</span>
            <div><b>{result.rule_based.detected ? `Failure at ${result.rule_based.module}` : 'No failure detected'}</b>
              <span>{result.rule_based.matched_rule}</span></div></div>
          <div className="rule-facts"><span>HTTP response <b>{result.rule_based.http_status ?? '—'}</b></span><span>Category <b>{result.rule_based.category || 'None'}</b></span></div></section></div>
      <div className="analysis-grid"><section className="card"><CardTitle title="Supporting evidence" meta="Observed telemetry used by the model" icon={<FileText size={17}/>}/>
        <ul className="evidence-list">{result.root_cause.supporting_evidence.map((e: string, i: number) => <li key={i}><CheckCircle2 size={15}/>{e}</li>)}</ul></section>
        <section className="card"><CardTitle title="SLA delay prediction" meta="Independent delay classifier" icon={<Clock3 size={17}/>}/>
          <div className="delay-callout"><div className={`delay-icon ${result.delay.likely_breach ? 'warn' : ''}`}><Clock3 size={18}/></div>
            <div><b>{result.delay.likely_breach ? 'Likely to breach SLA' : 'Currently on track'}</b>
              <span>{Math.round(result.delay.probability * 100)}% predicted breach probability</span></div>
            <span className="prob-pill">{Math.round(result.delay.threshold * 100)}% threshold</span></div>
          <p className="muted-copy">{result.delay.evidence.join(' ')}</p></section></div>
      <section className="card next-step"><div className="next-icon"><ShieldCheck size={19}/></div><div><span className="eyebrow">DETERMINISTIC SAFETY POLICY</span>
        <b>{result.recommended_action}</b><span>No LLM executes recovery. Payment, inventory, and validation decisions cannot be bypassed.</span></div>
        <Button kind="secondary" onClick={onExplain} disabled={busy}><Bot size={15}/> Explain incident</Button></section>
      {explanation && <section className="card explanation-card"><div className="explanation-head"><CardTitle title="Structured incident explanation" meta={explanation.source === 'optional-llm' ? 'Optional LLM · schema validated' : 'Local evidence template · no API key required'} icon={<Bot size={17}/>}/>
        <span className="model-chip">{explanation.source}</span></div>
        <div className="explanation-grid"><ExplainField title="What happened?" value={explanation.explanation.what_happened}/>
          <ExplainField title="Affected module" value={explanation.explanation.affected_module}/>
          <ExplainField title="Evidence" value={explanation.explanation.evidence.join(' ')}/>
          <ExplainField title="Likely impact" value={explanation.explanation.likely_impact}/>
          <ExplainField title="Recommended next step" value={explanation.explanation.next_step}/>
          <ExplainField title="Additional evidence needed" value={explanation.explanation.additional_evidence_needed.join(' ')}/></div>
        {explanation.note && <p className="muted-copy">{explanation.note}</p>}</section>}
    </>}</>
}
function ExplainField({ title, value }: { title: string; value: string }) {
  return <div className="explanation-field"><b>{title}</b><span>{value}</span></div>
}
function IncidentsPage({ incidents, onOpen }: any) {
  return <><Heading eyebrow="INVESTIGATION" title="Incident management" description="Correlated symptoms are grouped by service and telemetry. Correlation does not establish causation."/>
    <div className="incident-summary"><div><b>{incidents.length}</b><span>Incident groups</span></div><div><b>{incidents.reduce((n:number, i:Incident) => n+i.orders.length,0)}</b><span>Affected orders</span></div><div><b>{incidents.filter((i:Incident)=>i.severity==='HIGH').length}</b><span>High severity</span></div></div>
    <div className="incident-grid">{incidents.map((incident: Incident) => <section className="card incident-detail" key={incident.id}>
      <div className="incident-heading"><span className={`severity-mark ${incident.severity.toLowerCase()}`}/><div><span className="eyebrow">{incident.id} · {incident.status}</span>
        <h3>{incident.category.replaceAll('_',' ')} · {incident.module}</h3></div><span className={`severity-label ${incident.severity.toLowerCase()}`}>{incident.severity}</span></div>
      <p>{incident.probable_shared_cause}</p><div className="correlation-note"><AlertCircle size={15}/><span>Possible correlation based on shared symptoms. No confirmed causation.</span></div>
      <div className="affected-orders"><b>Affected orders</b>{incident.orders.map(id => <button onClick={() => onOpen(id)} key={id}>{id}<ArrowRight size={14}/></button>)}</div>
      <div className="incident-evidence">{incident.events.slice(0,3).map(e=><span key={e.order_id}>{e.order_id} · HTTP {e.http_status}</span>)}</div>
    </section>)}
      {!incidents.length && <div className="card empty-state large"><ShieldCheck size={28}/>No incident groups detected</div>}</div></>
}
function ResolutionPage({ actions, onDecision }: any) {
  return <><Heading eyebrow="SAFE SELF-HEALING" title="Resolution center" description="Every action is policy-gated, logged, and simulated. Approval never overrides hard business constraints."/>
    <div className="notice safety"><ShieldCheck size={17}/><span><b>Safety boundary</b> — hard payment and inventory rejections are never bypassed. Retry operations use idempotency keys and bounded attempts.</span></div>
    <section className="card table-card"><div className="table-head"><CardTitle title="Recommended & executed actions" meta={`${actions.length} audited actions`} icon={<ShieldCheck size={17}/>}/>
      <span className="risk-legend"><i className="risk-low"/>Low risk <i className="risk-high"/>Approval required</span></div>
      <div className="table-scroll"><table><thead><tr><th>ORDER</th><th>RECOMMENDED ACTION</th><th>RISK</th><th>STATUS</th><th>TRIGGER EVIDENCE</th><th>DECISION</th></tr></thead><tbody>
        {actions.map((a: Action) => <tr key={a.id}><td><b className="order-id">{a.order_id}</b></td><td className="action-cell">{a.action}</td>
          <td><span className={`risk-pill ${a.risk.toLowerCase()}`}>{a.risk}</span></td><td><Status value={a.status}/></td>
          <td>{a.evidence.http_status ? `HTTP ${a.evidence.http_status}` : 'Policy recommendation'}</td>
          <td>{a.status === 'PENDING_APPROVAL' ? <div className="decision-buttons"><button className="approve" onClick={() => onDecision(a.id,true)}><Check size={14}/> Approve</button>
            <button className="decline" onClick={() => onDecision(a.id,false)}>Decline</button></div> : a.result || '—'}</td></tr>)}
        {!actions.length && <tr><td colSpan={6} className="empty-row">No recovery actions yet. Simulate a fault to see safe recommendations.</td></tr>}
      </tbody></table></div></section></>
}
function ComparePage({ result, orders, selected, setSelected, onCompare, busy }: any) {
  return <><Heading eyebrow="MEASURED COMPARISON" title="AI vs rule-based analysis" description="Compare two approaches on the same observed order; ML complements rules, it does not replace them."/>
    <div className="analysis-select card"><label>Incident / order</label><select value={selected} onChange={e => setSelected(e.target.value)}>
      {orders.map((o:Order)=><option key={o.id} value={o.id}>{o.id} · {o.product} · {o.status}</option>)}</select><Button onClick={onCompare} disabled={!selected || busy}><Activity size={15}/> Run side-by-side</Button></div>
    {!result ? <div className="card empty-state large"><Layers3 size={28}/><b>Use the same incident for both systems</b><span>Results come from the selected order and held-out model evaluation.</span></div> :
      <><div className="compare-grid">{[result.system_a,result.system_b].map((sys:any,index:number)=><section className={`card compare-card ${index ? 'ai' : ''}`} key={sys.name}>
        <div className="compare-head"><div className={`compare-icon ${index ? 'blue' : ''}`}>{index?<Bot size={19}/>:<ListFilter size={19}/>}</div>
          <div><span className="eyebrow">SYSTEM {index?'B':'A'}</span><h2>{sys.name}</h2></div></div>
        <div className="compare-row"><span>Detection result</span><b>{sys.detection.detected ? `${sys.detection.module} · HTTP ${sys.detection.http_status}` : 'No failure observed'}</b></div>
        <div className="compare-row"><span>Root cause</span><b>{typeof sys.root_cause === 'string' ? sys.root_cause : sys.root_cause.prediction}</b></div>
        {index===1 && <><div className="compare-row"><span>Confidence</span><b>{Math.round(sys.root_cause.confidence*100)}%{sys.root_cause.prediction.startsWith('Unknown')?' · low evidence':''}</b></div>
          <div className="compare-row"><span>Delay probability</span><b>{Math.round(sys.delay.probability*100)}% · {sys.delay.likely_breach?'risk':'on track'}</b></div>
          <div className="evidence-box"><b>Supporting evidence</b>{sys.root_cause.supporting_evidence.map((e:string,i:number)=><span key={i}>• {e}</span>)}</div></>}
        {index===0 && <div className="compare-row"><span>Delay assessment</span><b>{sys.delay}</b></div>}
        <div className="compare-row"><span>Recommended action</span><b>{typeof sys.action === 'string' ? sys.action : 'See evidence-based safety recommendation'}</b></div>
        <div className="compare-disclaimer">{index ? 'Probabilistic hypothesis · held-out metrics shown below' : 'Exact known-code matching · thresholds and policies'}</div>
      </section>)}</div>
      <div className="notice"><AlertCircle size={17}/><span>AI adds probable root-cause hypotheses and delay probabilities. For a known status code, ordinary rules are sufficient to detect the failure; neither method proves causation.</span></div>
      <EvaluationCompact evaluation={result.evaluation}/></>}
  </>
}
function EvaluationCompact({ evaluation }: any) {
  if (!evaluation) return null
  return <section className="card eval-compact"><CardTitle title="Measured held-out evaluation" meta="Generated from the saved test split"/>
    <div className="eval-mini-grid">{[['Root-cause accuracy',evaluation.root_cause.accuracy,evaluation.root_cause.test_size],
      ['SLA model accuracy',evaluation.delay.accuracy,evaluation.delay.test_size]].map(([name,score,size]:any)=><div key={name}><span>{name}</span><b>{(score*100).toFixed(1)}%</b><small>{size} held-out records</small></div>)}</div></section>
}
function EvaluationPage({ evaluation }: any) {
  if (!evaluation) return <><Heading eyebrow="MODEL GOVERNANCE" title="Model evaluation" description="Measured performance on reproducible, held-out synthetic data."/><div className="card empty-state">Loading model metrics…</div></>
  const sections=[['Root-cause classification',evaluation.root_cause],['SLA breach prediction',evaluation.delay]]
  return <><Heading eyebrow="MODEL GOVERNANCE" title="Model evaluation" description="Measured performance on reproducible, held-out synthetic data. Metrics are not fabricated or hand-entered."/>
    <div className="notice"><Cpu size={17}/><span>Training and test sets use distinct rows; root cause labels are targets only and are excluded from model inputs. Stratified split, fixed seeds.</span></div>
    {sections.map(([name,m]:any,index:number)=><div className="eval-layout" key={name}><section className="card eval-card">
      <CardTitle title={name} meta={`${m.dataset_size.toLocaleString()} synthetic labeled rows · ${m.split}`} icon={index?<Clock3 size={17}/>:<Bot size={17}/>}/>
      <div className="eval-metrics"><div><span>Accuracy</span><b>{(m.accuracy*100).toFixed(1)}%</b></div><div><span>Train / test</span><b>{m.train_size.toLocaleString()} / {m.test_size.toLocaleString()}</b></div>
        <div><span>Evaluation split</span><b>75% / 25%</b></div></div>
      <div className="table-scroll"><table><thead><tr><th>CLASS</th><th>PRECISION</th><th>RECALL</th><th>F1 SCORE</th><th>SUPPORT</th></tr></thead><tbody>
        {m.per_class.map((r:any)=><tr key={r.label}><td><b>{r.label}</b></td><td>{(r.precision*100).toFixed(1)}%</td><td>{(r.recall*100).toFixed(1)}%</td>
          <td>{(r.f1*100).toFixed(1)}%</td><td>{r.support}</td></tr>)}</tbody></table></div></section>
      <section className="card matrix-card"><CardTitle title="Confusion matrix" meta="Rows = actual · columns = predicted"/>
        {(() => { const labels = m.labels || ['On track','SLA breach']; return <div className="matrix" style={{gridTemplateColumns:`92px repeat(${labels.length},minmax(34px,1fr))`}}>
          <div className="matrix-head"/>{labels.map((l:string)=><div className="matrix-head" key={l} title={l}>{l.slice(0,13)}</div>)}
          {labels.map((l:string,ri:number)=><div className="matrix-row" key={l}><b title={l}>{l.slice(0,13)}</b>{(m.confusion_matrix[ri]||[]).map((v:number,ci:number)=><span key={ci} style={{background:ri===ci?`rgba(49,142,112,${.08+v/Math.max(1,m.test_size)*2})`:undefined}}>{v}</span>)}</div>)}</div> })()}
        <div className="matrix-caption">Actual class <ArrowRight size={13}/> Predicted class</div></section></div>)}
    <section className="card model-caveat"><AlertCircle size={18}/><span><b>Interpretation note</b> Synthetic data has deliberately clear signal patterns. Real-world accuracy can be lower because production telemetry is noisier and distributions drift. Use confidence thresholds and operator investigation.</span></section>
  </>
}
function SettingsPage({ settings, onSave }: any) {
  const [retry, setRetry] = useState(settings?.retry_limit ?? 3)
  const [speed, setSpeed] = useState(settings?.simulation_speed ?? 1)
  const [inject, setInject] = useState(settings?.fault_injection_enabled ?? true)
  const [limits, setLimits] = useState<Record<string, number>>(settings?.stage_limits_ms ?? {})
  useEffect(() => { if(settings) { setRetry(settings.retry_limit);setSpeed(settings.simulation_speed);setInject(settings.fault_injection_enabled);setLimits(settings.stage_limits_ms) } }, [settings])
  return <><Heading eyebrow="CONFIGURATION" title="Settings" description="Tune operational thresholds for the local simulator. Retry attempts are capped for safety."/>
    <div className="settings-grid"><section className="card setting-card"><CardTitle title="Pipeline controls" meta="Applies to subsequent simulated orders" icon={<Settings2 size={17}/>}/>
      <label className="field-label">Maximum retry limit <span>Bounded to 1–5</span></label><input className="field" type="number" min="1" max="5" value={retry} onChange={e=>setRetry(Number(e.target.value))}/>
      <label className="field-label">Simulation speed <span>1×–10×</span></label><input className="field" type="number" min="1" max="10" value={speed} onChange={e=>setSpeed(Number(e.target.value))}/>
      <label className="toggle-row"><span><b>Allow fault injection</b><small>Faults only affect simulated services.</small></span><input type="checkbox" checked={inject} onChange={e=>setInject(e.target.checked)}/></label>
      <div className="settings-note"><ShieldCheck size={16}/> Hard payment and inventory rejection controls cannot be disabled.</div>
      <Button onClick={()=>onSave({retry_limit:retry,simulation_speed:speed,fault_injection_enabled:inject,stage_limits_ms:limits})}><Check size={15}/> Save settings</Button></section>
      <section className="card setting-card"><CardTitle title="Stage SLA thresholds" meta="Current configured time limits"/>
        {Object.entries(limits).map(([stage,ms])=><div className="sla-setting" key={stage}><span>{stage}</span>
          <label className="sla-input"><input type="number" min="100" max="600000" value={ms} onChange={e=>setLimits({...limits,[stage]:Number(e.target.value)})}/>ms</label>
          <div className="sla-track"><i style={{width:`${Math.min(100,ms/120)}%`}}/></div></div>)}
        <div className="settings-note"><Clock3 size={16}/> Delay probabilities are produced by the trained classifier, separate from fixed stage thresholds.</div></section></div>
    <section className="card"><CardTitle title="Integration mode" meta="Local-only by default"/><div className="integration-row"><div className="integration-icon"><Database size={18}/></div><div><b>SQLite database</b><span>Persistent local order, event, action, and audit history.</span></div><span className="connected"><i/>Connected</span></div>
      <div className="integration-row"><div className="integration-icon purple"><Cpu size={18}/></div><div><b>Scikit-learn models</b><span>Locally persisted root-cause and SLA models.</span></div><span className="connected"><i/>Active</span></div>
      <div className="integration-row"><div className="integration-icon gray"><Cloud size={18}/></div><div><b>LLM explanations</b><span>Optional adapter; deterministic evidence-based templates used without API configuration.</span></div><span className="optional-tag">OPTIONAL</span></div></section>
  </>
}

function Storefront({ onOrderCreated, tracking, onViewOrder, flash }: {
  onOrderCreated: (tracking: StoreTracking) => Promise<void>; tracking: StoreTracking;
  onViewOrder: (id: string) => void; flash: (message: string) => void
}) {
  const [products, setProducts] = useState<StoreProduct[]>([])
  const [storeOrders, setStoreOrders] = useState<StoreOrderSummary[]>([])
  const [cart, setCart] = useState<Record<string, number>>({})
  const [address, setAddress] = useState('10 Market Street, Sample City, 10001')
  const [payment, setPayment] = useState('demo_card')
  const [demoFault, setDemoFault] = useState('none')
  const [loading, setLoading] = useState(false)
  const [checkoutError, setCheckoutError] = useState('')
  const [placing, setPlacing] = useState(false)
  const [cartOpen, setCartOpen] = useState(false)
  const [inventoryTools, setInventoryTools] = useState(false)
  const [trackingId, setTrackingId] = useState('')
  const cartItems = products.filter(product => cart[product.sku] > 0)
  const totalItems = Object.values(cart).reduce((sum, quantity) => sum + quantity, 0)
  const totalCents = cartItems.reduce((sum, product) => sum + product.price_cents * cart[product.sku], 0)

  const loadProducts = useCallback(async () => {
    setLoading(true)
    try { setProducts(await request<StoreProduct[]>('/store/products')); setCheckoutError('') }
    catch (e) { setCheckoutError(e instanceof Error ? e.message : 'Could not load product inventory') }
    finally { setLoading(false) }
  }, [])
  const loadStoreOrders = useCallback(async () => {
    try { setStoreOrders(await request<StoreOrderSummary[]>('/store/orders?limit=8')) }
    catch (e) { setCheckoutError(e instanceof Error ? e.message : 'Could not load store order history') }
  }, [])
  useEffect(() => {
    void loadProducts()
    void loadStoreOrders()
  }, [loadProducts, loadStoreOrders])
  useEffect(() => {
    if (!tracking && storeOrders.length) {
      void selectStoreOrder(storeOrders[0].order_id)
    }
  }, [storeOrders, tracking])

  const changeQuantity = (sku: string, amount: number) => {
    setCart(current => {
      const quantity = Math.min(10, Math.max(0, (current[sku] || 0) + amount))
      return { ...current, [sku]: quantity }
    })
  }

  const checkout = async () => {
    if (!cartItems.length) return
    setPlacing(true)
    setCheckoutError('')
    try {
      const idempotencyKey = `web-${crypto.randomUUID()}`
      const result = await request<any>('/store/checkout', {
        method: 'POST',
        body: JSON.stringify({
          items: cartItems.map(product => ({ sku: product.sku, quantity: cart[product.sku] })),
          shipping_address: address, payment_method: payment, demo_fault: demoFault, idempotency_key: idempotencyKey,
        }),
      })
      const detail = await request<any>(`/store/orders/${result.order.id}`)
      await onOrderCreated({ order_id: result.order.id, status: detail.order.status, stage: detail.order.stage,
        events: detail.events, total_cents: result.store_order.total_cents })
      setCart({})
      setCartOpen(false)
      flash(result.duplicate ? 'This checkout was already received; no duplicate order was created.' : 'Checkout accepted. Watching the live order pipeline.')
      void loadProducts()
      void loadStoreOrders()
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Checkout could not be completed.'
      setCheckoutError(message)
    } finally { setPlacing(false) }
  }

  useEffect(() => {
    if (tracking?.order_id && (tracking.status === 'COMPLETED' || tracking.status === 'FAILED' ||
      tracking.status === 'DELAYED' || tracking.status === 'MANUAL_REVIEW_REQUIRED' ||
      tracking.events[tracking.events.length - 1]?.event_type === 'STORE_STOCK_RESTORED')) void loadProducts()
  }, [tracking?.status, tracking?.order_id,
    tracking?.events[tracking.events.length - 1]?.event_type, loadProducts])

  const selectStoreOrder = async (orderId: string) => {
    try {
      const result = await request<any>(`/store/orders/${encodeURIComponent(orderId)}`)
      setTrackingId(orderId)
      await onOrderCreated({ order_id: orderId, status: result.order.status, stage: result.order.stage,
        events: result.events, total_cents: result.store_order.total_cents })
    } catch (e) { flash(e instanceof Error ? e.message : 'Could not load this order timeline') }
  }

  const restock = async (product: StoreProduct) => {
    try {
      await request(`/store/products/${encodeURIComponent(product.sku)}/restock`, {
        method: 'POST', body: JSON.stringify({ quantity: 10, reason: 'Operator replenishment for storefront demo' }),
      })
      await loadProducts()
      flash(`${product.name}: added 10 units to inventory`)
    } catch (e) { flash(e instanceof Error ? e.message : 'Restock failed') }
  }

  return <>
    <Heading eyebrow="CONNECTED DEMO STORE · LIVE ORDER PIPELINE" title="Field & Form"
      description="A working demo storefront. Checkout creates a persisted order and streams each service-stage result into Sentinel."
      action={<button className={`store-cart-button ${cartOpen ? 'selected' : ''}`} onClick={() => setCartOpen(!cartOpen)}>
        <ShoppingCart size={17}/> Cart <span>{totalItems}</span>
      </button>}/>
    <div className="store-hero"><div className="store-hero-copy"><span>CURATED FOR EVERYDAY</span><h2>Good things.<br/>Thoughtful delivery.</h2>
      <p>Shop a few sample goods and see every checkout move through payment, inventory, delivery, and notification in real time.</p>
      <div className="store-live-note"><span className="live-dot"/>Store connected to Sentinel order monitoring</div></div>
      <div className="store-hero-art"><div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/>
        <div className="hero-box"><PackageCheck size={72}/><span>MADE TO MOVE</span></div><div className="art-spark">✳</div></div>
    </div>
    <div className="store-section-head"><div><span className="eyebrow">THE SAMPLE SHOP</span><h2>Designed for daily use</h2><p>Live catalog stock · simulated payment · local fulfillment</p></div>
      <div className="store-tools"><button className={`text-button ${inventoryTools?'tools-active':''}`} onClick={()=>setInventoryTools(!inventoryTools)}>
        <Settings2 size={14}/>{inventoryTools?'Hide inventory tools':'Inventory tools'}</button>
        <span className="stock-indicator"><span className="live-dot"/>Synced</span></div></div>
    {checkoutError && <div className="banner error-banner"><AlertCircle size={17}/><div><b>Store / checkout notice</b><span>{checkoutError}</span></div></div>}
    <div className="product-grid">{products.map((product, index) => <article className="product-card" key={product.sku}>
      <div className={`product-art product-${product.color}`}><span className="product-category">{product.category}</span>
        <div className={`product-object object-${index}`}><ShoppingBag size={46}/><span>{product.name.split(' ')[0]}</span></div>
        {product.stock <= 4 && product.stock > 0 && <span className="stock-low">Only {product.stock} left</span>}
        {product.stock === 0 && <span className="stock-low out">Out of stock</span>}</div>
      <div className="product-info"><div><h3>{product.name}</h3><p>{product.description}</p></div>
        <div className="product-purchase"><div><b>{formatMoney(product.price_cents)}</b><span className="product-stock">{product.stock} in stock</span></div>
          {cart[product.sku] ? <div className="quantity-stepper"><button aria-label="Remove one" onClick={() => changeQuantity(product.sku, -1)}><Minus size={13}/></button>
            <span>{cart[product.sku]}</span><button aria-label="Add one" disabled={cart[product.sku] >= Math.min(10, product.stock)} onClick={() => changeQuantity(product.sku, 1)}><Plus size={13}/></button></div> :
            <button className="add-button" disabled={product.stock === 0 || loading} onClick={() => changeQuantity(product.sku, 1)}><Plus size={14}/> Add</button>}
        </div>{inventoryTools&&<button className="restock-button" onClick={()=>restock(product)}><Plus size={12}/> Restock 10 units</button>}</div></article>)}
      {!products.length && <div className="card empty-state large"><ShoppingBag size={28}/>{loading ? 'Loading live catalog…' : 'No products available'}</div>}
    </div>
    <section className="card store-history"><div className="history-heading"><div><span className="eyebrow">ORDER HISTORY</span><h3>Recent store checkouts</h3></div>
      <form onSubmit={event=>{event.preventDefault();if(trackingId.trim())void selectStoreOrder(trackingId.trim())}}>
        <input aria-label="Track order ID" placeholder="Enter order ID" value={trackingId} onChange={event=>setTrackingId(event.target.value)}/>
        <button type="submit"><Search size={14}/> Track order</button></form></div>
      <div className="store-history-list">{storeOrders.map(order=><button className={`store-history-row ${tracking?.order_id===order.order_id?'selected':''}`}
        key={order.order_id} onClick={()=>void selectStoreOrder(order.order_id)}>
        <span className="history-order-icon"><PackageCheck size={15}/></span><span className="history-order-info"><b>{order.product_name}</b>
          <small>{order.order_id} · {new Date(order.created_at).toLocaleString()}</small></span>
        <Status value={order.status}/><b className="history-total">{formatMoney(order.total_cents)}</b><ArrowRight size={14}/></button>)}
        {!storeOrders.length&&<div className="history-empty">Your completed and recent store orders will appear here.</div>}</div>
    </section>
    <div className={`store-cart-panel ${cartOpen ? 'open' : ''}`}>
      <div className="cart-panel-head"><div><span className="eyebrow">DEMO CHECKOUT</span><h3>Your basket <span>{totalItems}</span></h3></div>
        <button className="icon-button" aria-label="Close cart" onClick={() => setCartOpen(false)}><X size={17}/></button></div>
      {!cartItems.length ? <div className="cart-empty"><ShoppingCart size={24}/><b>Your basket is empty</b><span>Add a product to try the live order connection.</span></div> : <>
        <div className="cart-items">{cartItems.map(product => <div className="cart-item" key={product.sku}><div className={`cart-thumb product-${product.color}`}><ShoppingBag size={17}/></div>
          <div className="cart-item-name"><b>{product.name}</b><span>{formatMoney(product.price_cents)} each</span></div>
          <div className="quantity-stepper"><button aria-label="Remove one" onClick={() => changeQuantity(product.sku,-1)}><Minus size={12}/></button>
            <span>{cart[product.sku]}</span><button aria-label="Add one" disabled={cart[product.sku]>=product.stock} onClick={() => changeQuantity(product.sku,1)}><Plus size={12}/></button></div>
        </div>)}</div>
        <div className="checkout-fields"><label><span><MapPin size={13}/> Demo shipping address</span><input value={address} onChange={e => setAddress(e.target.value)} placeholder="Street, City, postal code"/></label>
          <label><span><CreditCard size={13}/> Simulated payment</span><select value={payment} onChange={e=>setPayment(e.target.value)}>
            <option value="demo_card">Demo card · approved</option><option value="demo_declined">Demo card · decline</option></select></label>
          <label className="fault-demo-label"><span><Activity size={13}/> Optional fault injection</span><select value={demoFault} onChange={e=>setDemoFault(e.target.value)}>
            <option value="none">Normal checkout</option><option value="500">Inventory API · HTTP 500 (recovers)</option>
            <option value="503">Inventory API · HTTP 503 (recovers)</option><option value="504">Inventory API · HTTP 504 (recovers)</option>
            <option value="429">Rate limit · HTTP 429 (throttles)</option><option value="invalid_address">Invalid address · validation</option>
            <option value="payment_rejected">Payment rejection</option><option value="inventory_unavailable">Inventory rejection</option>
            <option value="queue_backlog">Delivery queue backlog · SLA risk</option><option value="database_bottleneck">Database latency</option>
          </select></label></div>
        <div className="cart-total"><span>Order total <small> · tax included for demo</small></span><b>{formatMoney(totalCents)}</b></div>
        <button className="checkout-button" disabled={placing || !address.trim()} onClick={checkout}>
          {placing ? <><span className="button-spinner"/> Sending checkout…</> : <>Place demo order <ArrowRight size={16}/></>}
        </button><p className="payment-disclaimer"><ShieldCheck size={13}/> No real payment or customer data is sent. This is a simulated checkout.</p>
      </>}
    </div>
    {tracking && <section className="card store-tracking"><div className="tracking-head"><div><span className="eyebrow">LIVE ORDER TRACKER · {tracking.order_id}</span>
      <h3>Checkout is in motion</h3></div><Status value={tracking.status}/></div>
      <div className="tracking-progress">{stages.map((stage,index)=>{const reached=stages.indexOf(tracking.stage)>=index;
        return <div className={`tracking-stage ${reached?'reached':''} ${tracking.stage===stage?'current':''}`} key={stage}>
          <span>{reached? <Check size={13}/>:index+1}</span><b>{stage}</b>{index<stages.length-1&&<i/>}</div>})}</div>
      <div className="live-log-head"><span><Radio size={14}/> Incoming service events</span><button className="text-button" onClick={()=>onViewOrder(tracking.order_id)}>Open full timeline <ArrowRight size={14}/></button></div>
      <div className="live-event-list">{tracking.events.slice(-12).reverse().map((event:any,index:number)=><div className={`live-event ${event.event_type==='ERROR'?'error':event.event_type==='RECOVERY'?'recovery':''}`} key={`${event.event_id||event.id}-${index}`}>
        <span className="live-event-dot"/><b>{event.stage}</b><span>{event.message}</span>{event.response_code&&<em>HTTP {event.response_code}</em>}</div>)}
        {tracking.events.length===0&&<p className="muted-copy">Waiting for the order-processing service to publish its first event…</p>}</div>
      <div className="tracking-foot"><span className="live-dot"/>Updates arrive over Server-Sent Events as each service runs.<b>{formatMoney(tracking.total_cents)}</b></div>
    </section>}
  </>
}

function formatMoney(cents: number) {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(cents / 100)
}
