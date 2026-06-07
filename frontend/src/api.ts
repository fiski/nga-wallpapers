// Thin wrapper around the pywebview Python bridge (window.pywebview.api).
// Falls back to an in-browser mock so the UI can be developed/designed with
// `npm run dev` in a normal browser (no Python needed).

export type Candidate = {
  id: number
  title: string
  artist: string
  artistLast: string
  date: string
  medium: string
  accession: string
  srcW: number
  srcH: number
  ratio: number
  thumb: string // iiifthumburl, rendered directly by the browser
  iiif: string // base IIIF url, used server-side to build the 4K download
}

export type Status = {
  ready: boolean
  loading: boolean
  error: string | null
  count: number
  defaultTerms: string[]
  defaultOutDir: string
}

export type FindParams = {
  terms: string[]
  classifications: string[]
  width: number
  height: number
  noDuplicates: boolean
  previewLimit: number
}

export type Facets = {
  classifications: { value: string; count: number }[]
  subjects: string[]
}

export type SubjectCount = { term: string; count: number }

export type SubjectCountParams = {
  classifications: string[]
  width: number
  height: number
  noDuplicates: boolean
}

export type DownloadParams = {
  items: Candidate[]
  outDir: string
  width: number
  height: number
}

export type DownloadResult = { ok: number; total: number; outDir: string }

export type Progress = { current: number; total: number; label: string }

type PyApi = {
  get_status: () => Promise<Status>
  get_facets: () => Promise<Facets>
  subject_counts: (params: SubjectCountParams) => Promise<SubjectCount[]>
  find: (params: FindParams) => Promise<Candidate[]>
  download: (params: DownloadParams) => Promise<DownloadResult>
  choose_folder: () => Promise<string | null>
  open_folder: (params: { path: string }) => Promise<boolean>
}

declare global {
  interface Window {
    pywebview?: { api: PyApi }
    __wallpaperProgress?: (p: Progress) => void
  }
}

// Detect the pywebview bridge. It is injected ASYNCHRONOUSLY and announced via the
// `pywebviewready` event — so we must wait, not check synchronously. We resolve to the real
// api as soon as `window.pywebview.api` exists (via the event AND a polling fallback, since
// the event may fire before our listener attaches). We only fall back to the mock if the
// bridge never appears within `timeoutMs` (i.e. a plain browser dev session). Relying on the
// user-agent is unreliable — Edge WebView2's UA does not contain "pywebview".
function detectApi(timeoutMs = 6000): Promise<PyApi | null> {
  if (typeof window === 'undefined') return Promise.resolve(null)
  return new Promise((resolve) => {
    let done = false
    const finish = (v: PyApi | null) => {
      if (done) return
      done = true
      clearInterval(timer)
      resolve(v)
    }
    const tryResolve = () => {
      if (window.pywebview?.api) {
        finish(window.pywebview.api)
        return true
      }
      return false
    }
    window.addEventListener('pywebviewready', tryResolve, { once: true })
    const start = Date.now()
    const timer = setInterval(() => {
      if (tryResolve()) return
      const elapsed = Date.now() - start
      // Give up to the mock only if the bridge object never appeared at all (plain browser).
      if (elapsed > timeoutMs && !('pywebview' in window)) finish(null)
      // Hard cap: never hang even if the bridge object exists but `.api` never attaches.
      else if (elapsed > timeoutMs * 3) finish(window.pywebview?.api ?? null)
    }, 100)
    tryResolve()
  })
}

// Resolves once to the real bridge, or null when running in a plain browser (→ mock).
const bridge: Promise<PyApi | null> = detectApi()

// ---- Browser mock (used only when no Python bridge is present) ----

const mockCandidates: Candidate[] = Array.from({ length: 12 }, (_, i) => ({
  id: i,
  title: `Sample Landscape ${i + 1}`,
  artist: 'Doe, Jane',
  artistLast: 'Doe',
  date: `18${10 + i}`,
  medium: 'oil on canvas',
  accession: `2007.94.${i + 1}`,
  srcW: 4000 + i * 10,
  srcH: 2400,
  ratio: 1.7,
  thumb: `https://picsum.photos/seed/nga${i}/220/124`,
  iiif: '',
}))

const mockApi: PyApi = {
  get_status: async () => ({
    ready: true,
    loading: false,
    error: null,
    count: mockCandidates.length,
    defaultTerms: [
      'landscape', 'nature', 'seascape', 'forest', 'mountain', 'garden',
      'river', 'pastoral', 'countryside', 'coastal', 'botanical', 'sky',
    ],
    defaultOutDir: 'C:\\wallpapers',
  }),
  get_facets: async () => ({
    classifications: [
      { value: 'Print', count: 64590 },
      { value: 'Photograph', count: 23347 },
      { value: 'Drawing', count: 18304 },
      { value: 'Painting', count: 4443 },
      { value: 'Sculpture', count: 4704 },
    ],
    subjects: [
      'landscape', 'portrait', 'genre', 'figure', 'architecture',
      'animal', 'religious', 'still life', 'mythology', 'allegory',
    ],
  }),
  subject_counts: async () => [
    { term: 'landscape', count: 8 },
    { term: 'portrait', count: 5 },
    { term: 'genre', count: 3 },
    { term: 'architecture', count: 2 },
  ],
  // Empty terms = browse all; the live backend applies the real filters.
  find: async () => mockCandidates,
  download: async ({ items, outDir }) => {
    for (let i = 0; i < items.length; i++) {
      await new Promise((r) => setTimeout(r, 150))
      window.__wallpaperProgress?.({
        current: i + 1,
        total: items.length,
        label: items[i].title,
      })
    }
    return { ok: items.length, total: items.length, outDir }
  },
  choose_folder: async () => 'C:\\wallpapers',
  open_folder: async () => true,
}

function api(): Promise<PyApi> {
  return bridge.then((a) => a ?? mockApi)
}

// Resolves to true when running against the browser mock (no Python bridge), false for the
// real pywebview bridge. Use this to drive UI that must reflect mock vs. live state.
export function whenReady(): Promise<boolean> {
  return bridge.then((a) => a === null)
}

export async function getStatus(): Promise<Status> {
  return (await api()).get_status()
}
export async function getFacets(): Promise<Facets> {
  return (await api()).get_facets()
}
export async function getSubjectCounts(params: SubjectCountParams): Promise<SubjectCount[]> {
  return (await api()).subject_counts(params)
}
export async function find(params: FindParams): Promise<Candidate[]> {
  return (await api()).find(params)
}
export async function download(params: DownloadParams): Promise<DownloadResult> {
  return (await api()).download(params)
}
export async function chooseFolder(): Promise<string | null> {
  return (await api()).choose_folder()
}
export async function openFolder(path: string): Promise<boolean> {
  return (await api()).open_folder({ path })
}

// Register a progress callback that the Python side invokes via evaluate_js.
export function onProgress(cb: (p: Progress) => void): () => void {
  window.__wallpaperProgress = cb
  return () => {
    if (window.__wallpaperProgress === cb) window.__wallpaperProgress = undefined
  }
}
