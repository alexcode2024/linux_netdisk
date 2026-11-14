import { FormEvent, type ChangeEvent, type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowRightLeft,
  ChevronRight,
  CloudUpload,
  Copy,
  Eye,
  Folder,
  FolderOpen,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  SortAsc,
  Sun,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import './styles.css'

type Root = { id: string; name: string; read_only: boolean; allow_upload: boolean }
type FileEntry = { name: string; relative_path: string; type: 'file' | 'directory' | 'symlink' | 'other'; size: number | null; modified_at: string; extension?: string | null }
type FileList = { root_id: string; path: string; items: FileEntry[]; page: number; page_size: number; total: number; sort: string; order: string }
type CloudProvider = 'quark' | 'baidu'
type CloudAuthStatus = { authenticated: boolean; message: string }
type CloudStatuses = Record<CloudProvider, CloudAuthStatus | null>
type Job = { id: string; status: string; cloud_provider: CloudProvider; source_name: string; remote_directory_id: string; remote_directory_path: string; conflict_policy: string; total_files: number; completed_files: number; failed_files: number; total_bytes: number; uploaded_bytes: number; current_file: string | null; speed_bytes_per_second: number; error_summary: string | null; created_at: string; started_at: string | null; finished_at: string | null; items?: JobItem[] }
type JobItem = { id: number; source_relative_path: string; source_type: string; file_size: number; uploaded_bytes: number; status: string; error_message: string | null; retry_count: number }
type RemoteEntry = { id: string; name: string; path: string; type: string }

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, { credentials: 'same-origin', ...init })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }))
    throw new Error(error.detail || '请求失败')
  }
  return response.json()
}

function useCloudStatuses() {
  const [statuses, setStatuses] = useState<CloudStatuses>({ quark: null, baidu: null })
  const refreshStatuses = async () => {
    await Promise.all((['quark', 'baidu'] as const).map(async provider => {
      try {
        const status = await api<CloudAuthStatus>(`/api/v1/cloud/${provider}/status`)
        setStatuses(previous => ({ ...previous, [provider]: status }))
      } catch {
        setStatuses(previous => ({ ...previous, [provider]: { authenticated: false, message: '无法连接状态接口' } }))
      }
    }))
  }
  useEffect(() => {
    void refreshStatuses()
    const interval = window.setInterval(() => { void refreshStatuses() }, 60_000)
    return () => window.clearInterval(interval)
  }, [])
  return { statuses, refreshStatuses }
}

const formatBytes = (value: number | null | undefined) => {
  if (value === null || value === undefined) return '—'
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** unit).toFixed(unit ? 1 : 0)} ${units[unit]}`
}

const formatDuration = (seconds: number) => {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.ceil(seconds)
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  const remainingSeconds = total % 60
  if (minutes < 60) return `${minutes} 分 ${remainingSeconds} 秒`
  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  if (hours < 24) return `${hours} 小时 ${remainingMinutes} 分`
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
}

const estimatedRemaining = (job: Job) => job.speed_bytes_per_second > 0 && job.total_bytes > job.uploaded_bytes ? formatDuration((job.total_bytes - job.uploaded_bytes) / job.speed_bytes_per_second) : '—'

const formatDate = (value: string) => new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))

const statusText: Record<string, string> = { pending: '等待中', preparing: '准备中', running: '上传中', retrying: '重试中', completed: '已完成', failed: '失败', cancelled: '已取消' }

type Theme = 'light' | 'dark'

function ThemeButton({ theme, onToggle, className = '' }: { theme: Theme; onToggle: () => void; className?: string }) {
  const label = theme === 'dark' ? '切换到白天模式' : '切换到黑暗模式'
  return <button className={`theme-toggle ${className}`} type="button" onClick={onToggle} title={label} aria-label={label} aria-pressed={theme === 'dark'}>{theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}</button>
}

function Login({ onLogin, theme, onToggleTheme }: { onLogin: (username: string) => void; theme: Theme; onToggleTheme: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      const result = await api<{ username: string }>('/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) })
      onLogin(result.username)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }
  return <div className="login-shell">
    <ThemeButton theme={theme} onToggle={onToggleTheme} className="login-theme-toggle" />
    <div className="login-card">
      <div className="brand-mark"><HardDrive size={26} /></div>
      <p className="eyebrow">PRIVATE LAN STORAGE</p>
      <h1>欢迎回到 NAS</h1>
      <p className="muted">登录后管理授权目录，并将文件备份到夸克网盘。</p>
      <form onSubmit={submit} className="login-form">
        <label>管理员账户<input autoFocus value={username} onChange={event => setUsername(event.target.value)} placeholder="输入用户名" /></label>
        <label>密码<input type="password" value={password} onChange={event => setPassword(event.target.value)} placeholder="输入密码" /></label>
        {error && <div className="form-error">{error}</div>}
        <button className="primary full" disabled={loading}>{loading ? '正在验证…' : '登录管理台'}</button>
      </form>
      <p className="login-note"><ShieldCheck size={15} /> 仅限局域网访问 · 会话采用 HttpOnly Cookie</p>
    </div>
  </div>
}

function App() {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = window.localStorage.getItem('nas-web-theme')
    if (saved === 'light' || saved === 'dark') return saved
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  const [username, setUsername] = useState<string | null>(null)
  const [checking, setChecking] = useState(true)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    window.localStorage.setItem('nas-web-theme', theme)
  }, [theme])
  useEffect(() => { api<{ username: string }>('/api/v1/auth/me').then(data => setUsername(data.username)).catch(() => undefined).finally(() => setChecking(false)) }, [])
  if (checking) return <div className="loading-screen"><div className="spinner" />加载中…</div>
  if (!username) return <Login onLogin={setUsername} theme={theme} onToggleTheme={() => setTheme(current => current === 'dark' ? 'light' : 'dark')} />
  return <Workspace username={username} theme={theme} onToggleTheme={() => setTheme(current => current === 'dark' ? 'light' : 'dark')} onLogout={() => setUsername(null)} />
}

function Workspace({ username, theme, onToggleTheme, onLogout }: { username: string; theme: Theme; onToggleTheme: () => void; onLogout: () => void }) {
  const [page, setPage] = useState<'files' | 'tasks' | 'settings'>('files')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { statuses, refreshStatuses } = useCloudStatuses()
  const [roots, setRoots] = useState<Root[]>([])
  const [activeRoot, setActiveRoot] = useState('')
  const [activePath, setActivePath] = useState('')
  const [refreshToken, setRefreshToken] = useState(0)
  useEffect(() => { api<Root[]>('/api/v1/roots').then(data => { setRoots(data); if (!activeRoot && data[0]) setActiveRoot(data[0].id) }).catch(() => undefined) }, [activeRoot])
  const navigate = (next: 'files' | 'tasks' | 'settings') => { setPage(next); setSidebarOpen(false) }
  const logout = async () => { await api('/api/v1/auth/logout', { method: 'POST' }).catch(() => undefined); onLogout() }
  return <div className="app-shell">
    <header className="topbar">
      <div className="topbar-left"><button className="icon-button mobile-menu" onClick={() => setSidebarOpen(value => !value)}><Menu size={20} /></button><div className="brand-inline"><div className="brand-mark small"><HardDrive size={18} /></div><span>Ubuntu文件网盘备份</span></div><span className="topbar-tag">文件管理</span></div>
    </header>
    <div className="body-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-scroll">
          <div className="side-section-label">工作区</div>
          <button className={`nav-item ${page === 'files' ? 'active' : ''}`} onClick={() => navigate('files')}><LayoutDashboard size={18} />文件管理</button>
          <button className={`nav-item ${page === 'tasks' ? 'active' : ''}`} onClick={() => navigate('tasks')}><CloudUpload size={18} />备份任务</button>
          <div className="sidebar-status-header"><div className="side-section-label">系统状态</div><button className="sidebar-status-refresh" aria-label="刷新状态" title="刷新状态" onClick={() => void refreshStatuses()}><RefreshCw size={14} /></button></div>
          <div className="sidebar-status-list">
            <div className="sidebar-status-item" title="当前使用单管理员账户，所有 API 需要会话认证。">
              <span className="sidebar-status-dot good" />
              <div className="sidebar-status-info"><div className="sidebar-status-line"><strong>访问安全</strong><span className="sidebar-status-badge good">正常</span></div><small>HttpOnly 会话已启用</small></div>
            </div>
            {(['quark', 'baidu'] as const).map(provider => {
              const status = statuses[provider]
              const name = provider === 'quark' ? '夸克网盘' : '百度网盘'
              const label = status === null ? '检查中' : status.authenticated ? '已授权' : '需处理'
              const stateClass = status === null ? 'loading' : status.authenticated ? 'good' : 'warning'
              return <div className="sidebar-status-item" key={provider} title={status?.message || `${name}状态检查中`}>
                <span className={`sidebar-status-dot ${stateClass}`} />
                <div className="sidebar-status-info">
                  <div className="sidebar-status-line"><strong>{name}</strong><span className={`sidebar-status-badge ${stateClass}`}>{label}</span></div>
                  <small>{status?.message || '正在检查授权状态…'}</small>
                  {provider === 'baidu' && status && !status.authenticated && <a className="sidebar-status-action" href="/api/v1/cloud/baidu/authorize">授权百度网盘</a>}
                </div>
              </div>
            })}
            <div className="sidebar-status-item" title="符号链接默认拒绝，删除能力默认关闭，上传使用原子替换。">
              <span className="sidebar-status-dot good" />
              <div className="sidebar-status-info"><div className="sidebar-status-line"><strong>文件保护</strong><span className="sidebar-status-badge good">正常</span></div><small>符号链接拒绝 · 删除默认关闭</small></div>
            </div>
            <div className="sidebar-status-item" title="当前启用会话认证、目录限制、路径校验和上传保护。">
              <span className="sidebar-status-dot good" />
              <div className="sidebar-status-info"><div className="sidebar-status-line"><strong>安全策略</strong><span className="sidebar-status-badge good">已启用</span></div><small>访问认证 · 目录限制 · 上传保护</small></div>
            </div>
          </div>
          <div className="side-section-label roots-label">授权目录</div>
          {roots.map(root => <button key={root.id} className={`root-item ${page === 'files' && activeRoot === root.id ? 'selected' : ''}`} onClick={() => { setActiveRoot(root.id); setActivePath(''); navigate('files') }}><Folder size={17} /><span>{root.name}</span></button>)}
        </div>
        <div className="sidebar-bottom">
          <div className="sidebar-control-row">
            <button className={`nav-item sidebar-settings ${page === 'settings' ? 'active' : ''}`} onClick={() => navigate('settings')} title="系统设置" aria-label="系统设置"><Settings size={18} /></button>
            <ThemeButton theme={theme} onToggle={onToggleTheme} />
          </div>
          <div className="sidebar-account">
          <div className="sidebar-account-user"><span className="avatar">{username.slice(0, 1).toUpperCase()}</span><span className="user-name" title={username}>{username}</span></div>
            <div className="sidebar-account-actions"><button className="text-button" onClick={logout}><LogOut size={16} />退出</button></div>
          </div>
        </div>
      </aside>
      <main className="main-content">
        {page === 'files' && <FileManager root={roots.find(root => root.id === activeRoot)} rootId={activeRoot} path={activePath} onPathChange={setActivePath} refreshToken={refreshToken} onRefresh={() => setRefreshToken(value => value + 1)} />}
        {page === 'tasks' && <TaskCenter />}
        {page === 'settings' && <SettingsPage statuses={statuses} onRefresh={refreshStatuses} />}
      </main>
    </div>
  </div>
}

function FileManager({ root, rootId, path, onPathChange, refreshToken, onRefresh }: { root?: Root; rootId: string; path: string; onPathChange: (path: string) => void; refreshToken: number; onRefresh: () => void }) {
  const [files, setFiles] = useState<FileList | null>(null)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sort, setSort] = useState<'name' | 'size' | 'modified'>('name')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [page, setPage] = useState(1)
  const [notice, setNotice] = useState('')
  const [modal, setModal] = useState<'mkdir' | 'backup' | null>(null)
  const [backupItems, setBackupItems] = useState<FileEntry[]>([])
  const [backupProvider, setBackupProvider] = useState<CloudProvider>('quark')
  const [loading, setLoading] = useState(false)
  const uploadRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!rootId) return
    setLoading(true)
    api<FileList>(`/api/v1/files?root=${encodeURIComponent(rootId)}&path=${encodeURIComponent(path)}&q=${encodeURIComponent(query)}&sort=${sort}&order=${order}&page=${page}&page_size=100`).then(data => { setFiles(data); setSelected(new Set()) }).catch(error => setNotice(error.message)).finally(() => setLoading(false))
  }, [rootId, path, query, sort, order, page, refreshToken])
  const selectedItems = useMemo(() => files?.items.filter(item => selected.has(item.relative_path)) ?? [], [files, selected])
  const openBackup = (items: FileEntry[], provider: CloudProvider) => { setBackupItems(items); setBackupProvider(provider); setModal('backup') }
  const breadcrumbs = path ? path.split('/') : []
  const toggleSort = (value: 'name' | 'size' | 'modified') => { if (value === sort) setOrder(current => current === 'asc' ? 'desc' : 'asc'); else { setSort(value); setOrder('asc') } }
  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    const body = new FormData(); body.append('file', file)
    try { await api(`/api/v1/files/upload?root=${encodeURIComponent(rootId)}&path=${encodeURIComponent(path)}`, { method: 'POST', body }); setNotice(`已上传 ${file.name}`); onRefresh() } catch (error) { setNotice(error instanceof Error ? error.message : '上传失败') } finally { event.target.value = '' }
  }
  const createFolder = async (name: string) => { await api('/api/v1/files/mkdir', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root_id: rootId, relative_path: path, name }) }); setModal(null); setNotice('文件夹已创建'); onRefresh() }
  if (!root) return <EmptyState icon={<FolderOpen size={30} />} title="没有可用的授权目录" description="请在服务器 app.yaml 中配置至少一个允许访问的目录。" />
  return <div className="page-content">
    <div className="page-heading"><div><p className="eyebrow">STORAGE / {root.name.toUpperCase()}</p><h1>{path ? path.split('/').at(-1) : root.name}</h1><p className="muted">浏览、整理和安全地备份你的本地文件。</p></div><button className="icon-button refresh-button" onClick={onRefresh}><RefreshCw size={18} /></button></div>
    <div className="breadcrumb-row"><button className="breadcrumb-home" onClick={() => onPathChange('')}>{root.name}</button>{breadcrumbs.map((part, index) => <span className="breadcrumb-part" key={`${part}-${index}`}><ChevronRight size={15} /><button onClick={() => onPathChange(breadcrumbs.slice(0, index + 1).join('/'))}>{part}</button></span>)}</div>
    {notice && <div className="notice"><span>{notice}</span><button onClick={() => setNotice('')}><X size={15} /></button></div>}
    <div className="toolbar"><div className="search-box"><Search size={17} /><input value={search} onChange={event => setSearch(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') setQuery(search) }} placeholder="搜索当前目录…" /></div><div className="toolbar-actions"><button className="secondary" onClick={() => setModal('mkdir')}><Folder size={17} />新建文件夹</button><input hidden ref={uploadRef} type="file" onChange={upload} /><button className="secondary" disabled={!root.allow_upload} onClick={() => uploadRef.current?.click()}><Upload size={17} />上传</button><button className="primary" disabled={!selectedItems.length} onClick={() => openBackup(selectedItems, 'quark')}><CloudUpload size={17} />备份到夸克{selectedItems.length ? ` (${selectedItems.length})` : ''}</button></div></div>
    <div className="table-card"><div className="table-head"><div className="check-cell"><input type="checkbox" checked={Boolean(files?.items.length && selected.size === files.items.length)} onChange={event => setSelected(event.target.checked ? new Set(files?.items.map(item => item.relative_path)) : new Set())} /></div><button className="sort-button name-header" onClick={() => toggleSort('name')}>文件名 <SortAsc size={14} className={sort === 'name' && order === 'desc' ? 'flip' : ''} /></button><button className="sort-button size-header" onClick={() => toggleSort('size')}>大小 <SortAsc size={14} className={sort === 'size' && order === 'desc' ? 'flip' : ''} /></button><button className="sort-button date-header" onClick={() => toggleSort('modified')}>修改时间 <SortAsc size={14} className={sort === 'modified' && order === 'desc' ? 'flip' : ''} /></button><span className="action-header">操作</span></div>{loading ? <div className="table-empty"><div className="spinner" />正在读取目录…</div> : files?.items.length ? files.items.map(item => <FileRow key={item.relative_path} item={item} rootId={rootId} checked={selected.has(item.relative_path)} onChecked={checked => setSelected(previous => { const next = new Set(previous); checked ? next.add(item.relative_path) : next.delete(item.relative_path); return next })} onOpen={() => item.type === 'directory' && onPathChange(item.relative_path)} onRefresh={onRefresh} onBackup={provider => openBackup([item], provider)} />) : <div className="table-empty"><FolderOpen size={28} /><span>这个目录还是空的</span><small>上传文件或创建一个新文件夹开始整理。</small></div>}<div className="table-footer"><span>{files?.total ?? 0} 个项目 · 第 {page} / {Math.max(1, Math.ceil((files?.total ?? 0) / 100))} 页</span><span className="pagination"><button disabled={page <= 1} onClick={() => setPage(value => value - 1)}><ArrowLeft size={14} />上一页</button><button disabled={page >= Math.ceil((files?.total ?? 0) / 100)} onClick={() => setPage(value => value + 1)}>下一页<ArrowRight size={14} /></button></span></div></div>
    {modal === 'mkdir' && <FolderModal onClose={() => setModal(null)} onSubmit={createFolder} />}
    {modal === 'backup' && <BackupModal rootId={rootId} items={backupItems} initialProvider={backupProvider} onClose={() => setModal(null)} onCreated={() => { setModal(null); setNotice('备份任务已创建，可在“备份任务”中查看进度') }} />}
  </div>
}

function FileRow({ item, rootId, checked, onChecked, onOpen, onRefresh, onBackup }: { item: FileEntry; rootId: string; checked: boolean; onChecked: (checked: boolean) => void; onOpen: () => void; onRefresh: () => void; onBackup: (provider: CloudProvider) => void }) {
  const rename = async () => {
    const newName = window.prompt('输入新的名称', item.name)
    if (!newName || newName === item.name) return
    try { await api('/api/v1/files/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root_id: rootId, relative_path: item.relative_path, new_name: newName }) }); onRefresh() } catch (error) { window.alert(error instanceof Error ? error.message : '重命名失败') }
  }
  const move = async () => {
    const destinationPath = window.prompt('输入授权根目录内的目标文件夹相对路径，根目录输入 /', '')
    if (destinationPath === null) return
    try { await api('/api/v1/files/move', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root_id: rootId, relative_path: item.relative_path, destination_path: destinationPath === '/' ? '' : destinationPath }) }); onRefresh() } catch (error) { window.alert(error instanceof Error ? error.message : '移动失败') }
  }
  const download = () => { if (item.type === 'file') window.location.assign(`/api/v1/files/download?root=${encodeURIComponent(rootId)}&path=${encodeURIComponent(item.relative_path)}`) }
  const preview = () => { if (item.type === 'file') window.open(`/api/v1/files/preview?root=${encodeURIComponent(rootId)}&path=${encodeURIComponent(item.relative_path)}`, '_blank', 'noopener,noreferrer') }
  return <div className="table-row"><div className="check-cell"><input type="checkbox" checked={checked} onChange={event => onChecked(event.target.checked)} /></div><button className={`file-name ${item.type === 'directory' ? 'is-folder' : ''}`} onDoubleClick={onOpen}>{item.type === 'directory' ? <Folder size={19} /> : <span className="file-type">{item.extension?.slice(0, 4).toUpperCase() || 'FILE'}</span>}<span>{item.name}</span></button><span className="size-cell">{formatBytes(item.size)}</span><span className="date-cell">{formatDate(item.modified_at)}</span><span className="row-actions"><button title="预览文件" aria-label="预览文件" disabled={item.type !== 'file'} onClick={preview}><Eye size={16} /></button><button title="下载文件" aria-label="下载文件" disabled={item.type !== 'file'} onClick={download}><ArrowDownToLine size={16} /></button><button title="重命名" aria-label="重命名" onClick={rename}><Pencil size={15} /></button><button title="移动到…" aria-label="移动到" onClick={move}><ArrowRightLeft size={16} /></button><button className="row-backup-button quark" title="备份到夸克网盘" onClick={() => onBackup('quark')}><CloudUpload size={14} /><span>夸克</span></button><button className="row-backup-button baidu" title="备份到百度网盘" onClick={() => onBackup('baidu')}><CloudUpload size={14} /><span>百度</span></button></span></div>
}

function FolderModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: (name: string) => Promise<void> }) {
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const submit = async (event: FormEvent) => { event.preventDefault(); if (!name.trim()) return setError('请输入文件夹名称'); try { await onSubmit(name.trim()) } catch (reason) { setError(reason instanceof Error ? reason.message : '创建失败') } }
  return <Modal title="新建文件夹" onClose={onClose}><form onSubmit={submit}><label>文件夹名称<input autoFocus value={name} onChange={event => setName(event.target.value)} placeholder="例如：待整理" /></label>{error && <div className="form-error">{error}</div>}<div className="modal-actions"><button type="button" className="secondary" onClick={onClose}>取消</button><button className="primary">创建文件夹</button></div></form></Modal>
}

function BackupModal({ rootId, items, initialProvider, onClose, onCreated }: { rootId: string; items: FileEntry[]; initialProvider: CloudProvider; onClose: () => void; onCreated: () => void }) {
  const [provider, setProvider] = useState<CloudProvider>(initialProvider)
  const [remotePath, setRemotePath] = useState('/')
  const [remoteId, setRemoteId] = useState('0')
  const [ancestors, setAncestors] = useState<{ id: string; path: string }[]>([])
  const [directories, setDirectories] = useState<RemoteEntry[]>([])
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  useEffect(() => {
    setAncestors([]); setDirectories([]); setError('')
    if (provider === 'quark') { setRemoteId('0'); setRemotePath('/'); return }
    setRemoteId(''); setRemotePath('')
    api<{ root_path: string | null }>('/api/v1/cloud/baidu/status').then(status => {
      if (!status.root_path) throw new Error('请先在服务器配置百度应用名称')
      setRemoteId(status.root_path); setRemotePath(status.root_path)
    }).catch(reason => setError(reason instanceof Error ? reason.message : '百度网盘尚未就绪'))
  }, [provider])
  useEffect(() => {
    if (provider === 'baidu' && !remotePath.startsWith('/apps/')) return
    api<RemoteEntry[]>(`/api/v1/cloud/${provider}/directories?fid=${encodeURIComponent(remoteId)}&path=${encodeURIComponent(remotePath)}`).then(setDirectories).catch(reason => setError(reason instanceof Error ? reason.message : '无法读取网盘目录'))
  }, [provider, remoteId, remotePath])
  const total = items.reduce((sum, item) => sum + (item.size ?? 0), 0)
  const totalIsEstimated = items.some(item => item.type === 'directory' || item.size === null)
  const submit = async () => { setCreating(true); setError(''); try { await api('/api/v1/backups', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items: items.map(item => ({ root_id: rootId, relative_path: item.relative_path, type: item.type })), cloud_provider: provider, remote_directory_id: remoteId, remote_directory_path: remotePath, conflict_policy: 'skip' }) }); onCreated() } catch (reason) { setError(reason instanceof Error ? reason.message : '创建任务失败') } finally { setCreating(false) } }
  const providerName = provider === 'quark' ? '夸克网盘' : '百度网盘'
  return <Modal title={`备份到${providerName}`} wide onClose={onClose}><div className="provider-select" role="group" aria-label="选择网盘"><button className={provider === 'quark' ? 'active' : ''} onClick={() => setProvider('quark')}>夸克网盘</button><button className={provider === 'baidu' ? 'active' : ''} onClick={() => setProvider('baidu')}>百度网盘</button></div><div className="backup-layout"><div><div className="modal-label">本次备份</div><div className="selection-summary"><strong>{items.length} 个项目</strong><span>{totalIsEstimated ? '目录大小将在任务中计算' : `估算大小 ${formatBytes(total)}`}</span></div><div className="selected-list">{items.map(item => <div key={item.relative_path}><Folder size={15} /><span>{item.relative_path}</span><small>{formatBytes(item.size)}</small></div>)}</div></div><div><div className="modal-label">{providerName}目标目录</div><div className="remote-path"><button className="link-button" disabled={!ancestors.length} onClick={() => { const parent = ancestors.at(-1); if (parent) { setRemoteId(parent.id); setRemotePath(parent.path); setAncestors(ancestors.slice(0, -1)) } }}>上级</button><CloudUpload size={15} />{remotePath}</div><div className="remote-list">{directories.filter(directory => directory.type === 'directory').length ? directories.filter(directory => directory.type === 'directory').map(directory => <button key={directory.id} onClick={() => { setAncestors([...ancestors, { id: remoteId, path: remotePath }]); setRemoteId(provider === 'baidu' ? directory.path : directory.id); setRemotePath(directory.path) }}><Folder size={16} />{directory.name}<ChevronRight size={15} /></button>) : <p className="muted remote-empty">无法读取目录或当前目录为空</p>}</div><p className="conflict-note"><ShieldCheck size={15} /> 冲突策略：跳过，不覆盖远端同名文件</p></div></div>{error && <div className="form-error backup-error">{error}</div>}<div className="modal-actions"><button className="secondary" onClick={onClose}>取消</button><button className="primary" disabled={creating} onClick={submit}><CloudUpload size={16} />{creating ? '正在创建…' : '开始备份'}</button></div></Modal>
}

function TaskCenter() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [busyJob, setBusyJob] = useState('')
  const load = () => api<Job[]>('/api/v1/backups').then(setJobs).catch(reason => setNotice(reason instanceof Error ? reason.message : '加载任务失败')).finally(() => setLoading(false))
  useEffect(() => { load(); const timer = window.setInterval(load, 4000); return () => window.clearInterval(timer) }, [])
  useEffect(() => { if (!selectedJob) return; const source = new EventSource(`/api/v1/backups/${selectedJob.id}/events`); source.addEventListener('progress', event => { const update = JSON.parse((event as MessageEvent).data) as Job; setSelectedJob(current => current?.id === update.id ? { ...current, ...update, items: current.items } : current) }); return () => source.close() }, [selectedJob?.id])
  const runAction = async (job: Job, action: 'retry' | 'cancel') => {
    setBusyJob(job.id)
    try {
      const updated = await api<Job>(`/api/v1/backups/${job.id}/${action}`, { method: 'POST' })
      setNotice(action === 'retry' ? '已将失败项目放回队列' : '已请求取消任务')
      if (selectedJob?.id === job.id) setSelectedJob(updated)
      await load()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : action === 'retry' ? '重试失败' : '取消失败')
    } finally {
      setBusyJob('')
    }
  }
  const retry = (job: Job) => runAction(job, 'retry')
  const cancel = (job: Job) => runAction(job, 'cancel')
  const remove = async (job: Job) => {
    if (!window.confirm(`确定删除${job.status === 'cancelled' ? '已取消' : '失败'}任务“${job.source_name || '备份任务'}”吗？删除后无法恢复。`)) return
    setBusyJob(job.id)
    try {
      await api<void>(`/api/v1/backups/${job.id}`, { method: 'DELETE' })
      if (selectedJob?.id === job.id) setSelectedJob(null)
      setNotice('失败任务已删除')
      await load()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '删除任务失败')
    } finally {
      setBusyJob('')
    }
  }
  const openJob = async (jobId: string) => {
    try { setSelectedJob(await api<Job>(`/api/v1/backups/${jobId}`)) }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : '读取任务详情失败') }
  }
  return <div className="page-content"><div className="page-heading"><div><p className="eyebrow">CLOUD BACKUPS</p><h1>备份任务</h1><p className="muted">后台任务独立运行，关闭浏览器也不会中断上传。</p></div><button className="secondary" onClick={load} disabled={loading}><RefreshCw size={17} />刷新</button></div>{notice && <div className="notice"><span>{notice}</span><button onClick={() => setNotice('')}><X size={15} /></button></div>}<div className="stats-grid"><Stat label="全部任务" value={jobs.length} /><Stat label="进行中" value={jobs.filter(job => ['pending', 'preparing', 'running', 'retrying'].includes(job.status)).length} accent="blue" /><Stat label="已完成" value={jobs.filter(job => job.status === 'completed').length} accent="green" /><Stat label="需处理" value={jobs.filter(job => job.status === 'failed').length} accent="orange" /></div><div className="table-card tasks-table"><div className="table-head"><span className="task-id-header">任务</span><span>大小</span><span>进度</span><span>状态</span><span className="action-header">操作</span></div>{loading && jobs.length === 0 ? <div className="table-empty"><div className="spinner" />正在读取任务…</div> : jobs.length ? jobs.map(job => <div className="task-row" key={job.id}><button className="task-id" title={job.source_name || '备份任务'} aria-label={`${job.source_name || '备份任务'}，任务详情`} onClick={() => openJob(job.id)}><span className="job-icon"><CloudUpload size={16} /></span><span className="task-id-copy"><strong>{job.source_name || '备份任务'}</strong><small>{job.id.slice(0, 8)} · {formatDate(job.created_at)}</small></span></button><span className="task-size" title={formatBytes(job.total_bytes)}>{formatBytes(job.total_bytes)}</span><span className="task-progress"><div className="progress-label"><span>{job.completed_files}/{job.total_files} 个文件</span><span>{job.total_bytes ? `${Math.round(job.uploaded_bytes / job.total_bytes * 100)}%` : '0%'}</span></div><div className="progress-track"><span style={{ width: `${job.total_bytes ? Math.min(100, job.uploaded_bytes / job.total_bytes * 100) : 0}%` }} /></div><small className="task-progress-meta">{job.speed_bytes_per_second > 0 ? `${formatBytes(job.speed_bytes_per_second)}/秒` : '—'} · 预计剩余 {estimatedRemaining(job)}</small></span><span><span className={`status-pill ${job.status}`}>{statusText[job.status] || job.status}</span></span><span className="task-actions"><button className="link-button" onClick={() => openJob(job.id)}>详情</button>{job.status === 'failed' && <button className="link-button" disabled={busyJob === job.id} onClick={() => retry(job)}>重试</button>}{['failed', 'cancelled'].includes(job.status) && <button className="link-button danger-link" disabled={busyJob === job.id} onClick={() => remove(job)} title={`删除${job.status === 'cancelled' ? '已取消' : '失败'}任务`}><Trash2 size={14} />删除</button>}{['pending', 'preparing', 'running', 'retrying'].includes(job.status) && <button className="link-button danger-link" disabled={busyJob === job.id} onClick={() => cancel(job)}>取消</button>}</span></div>) : <div className="table-empty"><CloudUpload size={28} /><span>还没有备份任务</span><small>在文件管理中选择文件，点击“备份到网盘”开始。</small></div>}</div>{selectedJob && <JobDetail job={selectedJob} busy={busyJob === selectedJob.id} onClose={() => setSelectedJob(null)} onRetry={() => retry(selectedJob)} onCancel={() => cancel(selectedJob)} />}</div>
}

function JobDetail({ job, busy, onClose, onRetry, onCancel }: { job: Job; busy: boolean; onClose: () => void; onRetry: () => void; onCancel: () => void }) {
  return <Modal title={`任务 ${job.id.slice(0, 8)}`} wide onClose={onClose}><div className="job-overview"><div><span>状态</span><strong className={`status-text ${job.status}`}>{statusText[job.status] || job.status}</strong></div><div><span>网盘 / 目标目录</span><strong>{job.cloud_provider === 'baidu' ? '百度网盘' : '夸克网盘'} · {job.remote_directory_path}</strong></div><div><span>总大小</span><strong>{formatBytes(job.total_bytes)}</strong></div><div><span>上传速率</span><strong>{job.speed_bytes_per_second > 0 ? `${formatBytes(job.speed_bytes_per_second)}/秒` : '—'}</strong></div><div><span>预计剩余</span><strong>{estimatedRemaining(job)}</strong></div><div><span>错误项目</span><strong>{job.failed_files}</strong></div></div>{job.current_file && <div className="notice"><span>当前文件：{job.current_file}</span></div>}{job.error_summary && <div className="form-error">{job.error_summary}</div>}<div className="detail-items">{job.items?.map(item => <div key={item.id}><span className={`mini-status ${item.status}`} /> <span>{item.source_relative_path}</span><small>{item.status === 'completed' ? '完成' : item.error_message || statusText[item.status] || item.status}</small></div>)}</div><div className="modal-actions"><button className="secondary" onClick={onClose}>关闭</button>{['pending', 'preparing', 'running', 'retrying'].includes(job.status) && <button className="secondary danger-button" disabled={busy} onClick={onCancel}>取消任务</button>}{job.status === 'failed' && <button className="primary" disabled={busy} onClick={onRetry}>重试失败项</button>}</div></Modal>
}

function SettingsPage({ statuses, onRefresh }: { statuses: CloudStatuses; onRefresh: () => Promise<void> }) {
  const authResult = new URLSearchParams(window.location.search).get('baidu_auth')
  useEffect(() => { if (authResult) window.history.replaceState({}, '', window.location.pathname) }, [authResult])
  return <div className="page-content"><div className="page-heading"><div><p className="eyebrow">SYSTEM / SECURITY</p><h1>系统设置</h1><p className="muted">云盘连接与安全状态常驻显示在左侧导航栏。</p></div><button className="secondary" onClick={() => void onRefresh()}><RefreshCw size={16} />刷新状态</button></div>{authResult && <div className={authResult === 'success' ? 'notice' : 'form-error'}>{authResult === 'success' ? '百度网盘授权完成' : '百度网盘授权未完成，请重试'}</div>}<div className="info-panel"><div><h3>服务器运维</h3><p>云盘应用密钥和授权凭据只保存在服务器端受限目录，不要将 SecretKey、SignKey 或 Token 分享到聊天或网页。</p></div><button className="secondary" onClick={() => window.open('/api/docs', '_blank')}>打开 API 文档 <ArrowRight size={16} /></button></div></div>
}

function Stat({ label, value, accent = '' }: { label: string; value: number; accent?: string }) { return <div className="stat-card"><span className={`stat-dot ${accent}`} /><div><span>{label}</span><strong>{value}</strong></div></div> }
function EmptyState({ icon, title, description }: { icon: ReactNode; title: string; description: string }) { return <div className="empty-state"><div className="empty-icon">{icon}</div><h2>{title}</h2><p>{description}</p></div> }
function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) { return <div className="modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}><div className={`modal-card ${wide ? 'wide' : ''}`}><div className="modal-header"><h2>{title}</h2><button className="icon-button" onClick={onClose}><X size={19} /></button></div>{children}</div></div> }

export default App

const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('页面根节点 #root 不存在')
createRoot(rootElement).render(<App />)
