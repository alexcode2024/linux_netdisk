import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronRight, Copy, File, Folder, FolderOpen, RefreshCw, Share2, X } from 'lucide-react'
import { api } from './api'

type RemoteEntry = { id: string; name: string; path: string; type: string; size?: number | null; modified_at?: number | null }
type ShareResult = { share_url: string; passcode: string | null }
const rootFolder: RemoteEntry = { id: '0', name: '全部文件', path: '/', type: 'directory' }
const expirationOptions = [[1, '永久有效'], [2, '1 天'], [3, '7 天'], [4, '30 天'], [5, '60 天'], [6, '100 天'], [7, '180 天']] as const
const sortedEntries = (entries: RemoteEntry[]) => [...entries].sort((left, right) =>
  Number(right.type === 'directory') - Number(left.type === 'directory') || left.name.localeCompare(right.name, 'zh-CN'))

function formatSize(value: number | null | undefined) {
  if (value == null) return '—'
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** unit).toFixed(unit ? 1 : 0)} ${units[unit]}`
}

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(value); return } catch {}
  }
  const previousFocus = document.activeElement
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  try {
    textarea.select()
    if (!document.execCommand('copy')) throw new Error('复制失败，请手动选择链接和提取码复制')
  } finally {
    textarea.remove()
    if (previousFocus instanceof HTMLElement) previousFocus.focus()
  }
}

function ShareDialog({ entry, onClose }: { entry: RemoteEntry; onClose: () => void }) {
  const [urlType, setUrlType] = useState(2)
  const [expiredType, setExpiredType] = useState(1)
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<ShareResult | null>(null)
  const [error, setError] = useState('')
  const [copyNotice, setCopyNotice] = useState('')
  const submitting = useRef(false)
  const closeButton = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const previousFocus = document.activeElement
    closeButton.current?.focus()
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && !submitting.current) onClose() }
    window.addEventListener('keydown', escape)
    return () => {
      window.removeEventListener('keydown', escape)
      if (previousFocus instanceof HTMLElement) previousFocus.focus()
    }
  }, [onClose])
  const createShare = async () => {
    if (submitting.current || result) return
    submitting.current = true
    setPending(true)
    setError('')
    try {
      setResult(await api<ShareResult>('/api/v1/cloud/quark/shares', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_id: entry.id, url_type: urlType, expired_type: expiredType }),
      }))
    } catch (reason) { setError(reason instanceof Error ? reason.message : '创建分享失败') }
    finally { submitting.current = false; setPending(false) }
  }
  const copy = async () => {
    if (!result) return
    try {
      await copyText(`链接：${result.share_url}${result.passcode ? `\n提取码：${result.passcode}` : ''}`)
      setCopyNotice('分享信息已复制')
    } catch (reason) { setCopyNotice(reason instanceof Error ? reason.message : '复制失败，请手动复制') }
  }
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !pending) onClose() }}>
    <div className="modal-card quark-share-dialog" role="dialog" aria-modal="true" aria-labelledby="quark-share-title" onKeyDown={event => {
      if (event.key !== 'Tab') return
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input, select:not(:disabled), a[href]'))
      const first = controls[0]
      const last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }}>
      <div className="modal-header"><h2 id="quark-share-title">分享{entry.type === 'directory' ? '文件夹' : '文件'}</h2><button ref={closeButton} className="icon-button" aria-label="关闭分享窗口" disabled={pending} onClick={onClose}><X size={19} /></button></div>
      <p className="quark-share-name" title={entry.path}>{entry.name}</p>
      {result ? <div className="quark-share-result">
        <p className="muted">分享链接已生成 · {expirationOptions.find(([value]) => value === expiredType)?.[1]}</p>
        <div className="quark-share-link"><span>分享链接</span><p className="quark-share-url">{result.share_url}</p></div>
        {result.passcode && <label>提取码<input readOnly value={result.passcode} onFocus={event => event.target.select()} /></label>}
        <div role="status">{copyNotice}</div>
      </div> : <div className="quark-share-fields">
        <label>分享方式<select aria-label="分享方式" value={urlType} disabled={pending} onChange={event => setUrlType(Number(event.target.value))}><option value={2}>需要提取码</option><option value={1}>公开链接</option></select></label>
        <label>有效期<select aria-label="有效期" value={expiredType} disabled={pending} onChange={event => setExpiredType(Number(event.target.value))}>{expirationOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <p className="muted">{urlType === 2 ? '提取码由夸克自动生成。' : '获得链接的人可以访问分享内容。'}</p>
      </div>}
      {error && <div className="form-error" role="alert">{error}</div>}
      <div className="modal-actions"><button className="secondary" disabled={pending} onClick={onClose}>关闭</button>{result ? <button className="primary" onClick={() => void copy()}><Copy size={16} />复制分享信息</button> : <button className="primary" disabled={pending} onClick={() => void createShare()}><Share2 size={16} />{pending ? '正在生成…' : '生成分享链接'}</button>}</div>
    </div>
  </div>
}

export function QuarkDrive() {
  const [ancestors, setAncestors] = useState<RemoteEntry[]>([rootFolder])
  const [cache, setCache] = useState<Record<string, RemoteEntry[]>>({})
  const [loading, setLoading] = useState<Set<string>>(new Set())
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [shareEntry, setShareEntry] = useState<RemoteEntry | null>(null)
  const cached = useRef<Record<string, RemoteEntry[]>>({})
  const inFlight = useRef(new Map<string, Promise<void>>())
  const closeShare = useCallback(() => setShareEntry(null), [])
  const selected = ancestors[ancestors.length - 1]
  const loadDirectory = useCallback((folder: RemoteEntry, force = false): Promise<void> => {
    const running = inFlight.current.get(folder.id)
    if (running) return running
    if (!force && cached.current[folder.id]) return Promise.resolve()
    setLoading(previous => new Set(previous).add(folder.id))
    setErrors(previous => ({ ...previous, [folder.id]: '' }))
    const request = api<RemoteEntry[]>(`/api/v1/cloud/quark/directories?fid=${encodeURIComponent(folder.id)}&path=${encodeURIComponent(folder.path)}`)
      .then(entries => {
        const sorted = sortedEntries(entries)
        cached.current = { ...cached.current, [folder.id]: sorted }
        setCache(previous => ({ ...previous, [folder.id]: sorted }))
      })
      .catch(reason => setErrors(previous => ({ ...previous, [folder.id]: reason instanceof Error ? reason.message : '读取目录失败' })))
      .finally(() => {
        inFlight.current.delete(folder.id)
        setLoading(previous => { const next = new Set(previous); next.delete(folder.id); return next })
      })
    inFlight.current.set(folder.id, request)
    return request
  }, [])
  useEffect(() => { void loadDirectory(selected) }, [selected, loadDirectory])
  const openFolder = (trail: RemoteEntry[]) => setAncestors(trail)
  const entries = cache[selected.id]
  return <div className="page-content quark-page">
      <section className="quark-files" aria-label="夸克网盘文件列表">
        <div className="breadcrumb-toolbar"><div className="breadcrumb-row quark-breadcrumb">{ancestors.map((folder, index) => <span className="breadcrumb-part" key={folder.id}>{index > 0 && <ChevronRight size={14} />}<button title={folder.path} onClick={() => openFolder(ancestors.slice(0, index + 1))}>{folder.name}</button></span>)}</div><button className="icon-button refresh-button" disabled={loading.has(selected.id)} onClick={() => void loadDirectory(selected, true)} aria-label="刷新夸克网盘" title="刷新夸克网盘"><RefreshCw size={17} /></button></div>
        {errors[selected.id] && <div className="form-error" role="alert">{errors[selected.id]} <button className="link-button" onClick={() => void loadDirectory(selected, true)}>重试</button></div>}
        <div className="table-card quark-file-table">
          <div className="table-head quark-file-grid"><span>名称</span><span>大小</span><span>修改时间</span><span className="action-header">操作</span></div>
          {loading.has(selected.id) && !entries ? <div className="table-empty"><div className="spinner" />正在读取网盘目录…</div> : entries?.length ? entries.map(entry => <div className="quark-file-row quark-file-grid" key={entry.id}>
            {entry.type === 'directory' ? <button className="quark-file-name" title={entry.name} onClick={() => openFolder([...ancestors, entry])}><Folder size={19} /><span>{entry.name}</span></button> : <span className="quark-file-name" title={entry.name}><File size={19} /><span>{entry.name}</span></span>}
            <span>{entry.type === 'directory' ? '—' : formatSize(entry.size)}</span>
            <span>{entry.modified_at ? new Date(entry.modified_at).toLocaleString('zh-CN', { hour12: false }) : '—'}</span>
            <span className="quark-file-actions"><button className="link-button" aria-label={`分享${entry.name}`} onClick={() => setShareEntry(entry)}><Share2 size={15} />分享</button></span>
          </div>) : !loading.has(selected.id) && !errors[selected.id] && <div className="table-empty"><FolderOpen size={28} /><span>此文件夹为空</span></div>}
        </div>
      </section>
    {shareEntry && <ShareDialog entry={shareEntry} onClose={closeShare} />}
  </div>
}
