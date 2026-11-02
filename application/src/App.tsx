import { WorkspaceReset } from './components/WorkspaceReset'
import { useState } from 'react'
import { Database, FileSpreadsheet, Layers3 } from 'lucide-react'
import { MetadataWorkspace, tabs, type Tab } from './metadata/MetadataWorkspace'
import { DataPreview } from './components/DataPreview'
import { TableCatalog } from './components/TableCatalog'
import { UploadPanel } from './components/UploadPanel'
import { useSources } from './hooks/useSources'

export default function App() {
  const [tab, setTab] = useState<Tab>(() => {
    const value = new URLSearchParams(location.search).get('tab')
    return tabs.find((t) => t.id === value)?.id ?? 'sources'
  })
  const onTab = (value: Tab) => {
    setTab(value)
    const url = new URL(location.href)
    url.searchParams.set('tab', value)
    history.replaceState(null, '', url)
  }
  const sources = useSources()
  const connected = sources.status?.connected
  return (
    <div className="application-shell">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Layers3 size={22} strokeWidth={1.7} aria-hidden="true" />
          </div>
          <span>Semantic</span>
        </div>
        <div className="workspace-label">DATA WORKSPACE</div>
        <nav aria-label="Workspace">
          {tabs.map((item) => (
            <button
              key={item.id}
              className={'nav-item ' + (tab === item.id ? 'active' : '')}
              onClick={() => onTab(item.id)}
              aria-current={tab === item.id ? 'page' : undefined}
            >
              <Database size={18} aria-hidden="true" />
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <FileSpreadsheet size={19} aria-hidden="true" />
          <p>
            Good models start
            <br />
            with good source data.
          </p>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span>
            Workspace <span className="breadcrumb-divider">/</span>{' '}
            <strong>{tabs.find((t) => t.id === tab)?.label}</strong>
          </span>
          <div
            className={`connection-status ${connected ? 'connected' : sources.refreshing ? 'checking' : 'disconnected'}`}
            role="status"
          >
            <span className="connection-dot" aria-hidden="true" />
            {connected
              ? 'ClickHouse connected'
              : sources.refreshing
                ? 'Checking connection'
                : 'Connection unavailable'}
          </div>
        </header>
        <main id="main-content">
          <div className="page-heading">
            <h1>{tabs.find((t) => t.id === tab)?.label}</h1>
            <p>
              {tab === 'sources'
                ? 'Bring your data in. Explore it before you start modeling.'
                : tab === 'planning-view'
                  ? 'Explore dimension attributes and measures in a planning pivot.'
                  : tab === 'materialise'
                    ? 'Build your semantic network and explore planning-level tables.'
                    : 'Capture the meaning, grain, and policies behind your data.'}
            </p>
          </div>
          {tab === 'sources' && <WorkspaceReset />}
          <MetadataWorkspace tab={tab} onTab={onTab}>
            {(management) => (
              <>
                <div className="source-workspace">
                  <UploadPanel
                    maxRows={sources.status?.maxCsvRows ?? 5000}
                    maxBytes={sources.status?.maxFileBytes ?? 10_485_760}
                    onImported={sources.imported}
                  />
                  <TableCatalog
                    management={management}
                    tables={sources.tables}
                    selected={sources.selected}
                    loading={sources.refreshing}
                    error={sources.catalogError}
                    truncated={sources.truncated}
                    onSelect={sources.select}
                    onRefresh={() => void sources.refresh()}
                  />
                </div>
                {sources.selected && (
                  <DataPreview
                    table={sources.selected}
                    preview={sources.preview}
                    loading={sources.previewLoading}
                    error={sources.previewError}
                    offset={sources.offset}
                    limit={sources.limit}
                    onOffset={sources.setOffset}
                    onLimit={sources.changeLimit}
                    onRefresh={sources.refreshPreview}
                  />
                )}
              </>
            )}
          </MetadataWorkspace>
        </main>
        <footer className="app-footer">
          <span>Semantic workspace</span>
          <span>
            CSV uploads · {sources.status?.maxCsvRows.toLocaleString() ?? '5,000'} rows per file
          </span>
        </footer>
      </div>
    </div>
  )
}
