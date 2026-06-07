import { useEffect, useId, useMemo, useState } from 'react'
import { Autocomplete } from '@base-ui/react/autocomplete'
import { Checkbox } from '@base-ui/react/checkbox'
import { CheckboxGroup } from '@base-ui/react/checkbox-group'
import { Dialog } from '@base-ui/react/dialog'
import { Field } from '@base-ui/react/field'
import { NumberField } from '@base-ui/react/number-field'
import { Popover } from '@base-ui/react/popover'
import { Progress } from '@base-ui/react/progress'
import { ScrollArea } from '@base-ui/react/scroll-area'
import { Select } from '@base-ui/react/select'
import { Switch } from '@base-ui/react/switch'
import { Toast } from '@base-ui/react/toast'
import {
  type Candidate,
  type Facets,
  type Progress as ProgressData,
  type Status,
  type SubjectCount,
  chooseFolder,
  download,
  find,
  getFacets,
  getStatus,
  getSubjectCounts,
  onProgress,
  openFolder,
  whenReady,
} from './api'

// PLACEHOLDER UI built on Base UI primitives. Proves the data-load / find /
// download plumbing end-to-end and exercises the components we'll reuse when
// rebuilding the real UI from the Figma design. Visuals here are intentionally
// plain — the bridge in src/api.ts is what matters and stays put.

// Preset 16:9 output resolutions (replaces manual width/height entry).
const RESOLUTIONS = [
  { value: '1080p', label: '1080p · 1920×1080', w: 1920, h: 1080 },
  { value: '1440p', label: '1440p · 2560×1440', w: 2560, h: 1440 },
  { value: '4k', label: '4K · 3840×2160', w: 3840, h: 2160 },
  { value: '5k', label: '5K · 5120×2880', w: 5120, h: 2880 },
  { value: 'uw', label: 'Ultrawide · 3440×1440 (21:9)', w: 3440, h: 1440 },
] as const
const RES_ITEMS = RESOLUTIONS.map(({ value, label }) => ({ value, label }))

const btn =
  'h-9 shrink-0 whitespace-nowrap rounded-md px-4 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed'
const btnPrimary = `${btn} bg-neutral-900 text-white hover:bg-neutral-700`
const btnGhost = `${btn} border border-neutral-300 bg-white text-neutral-900 hover:bg-neutral-100`
const labelCls = 'text-xs font-medium text-neutral-600'

// Build a preview image from a candidate's IIIF base ("best fit within 800×800,
// preserve aspect"). 800px covers the modal's max display size with no visible quality
// loss while rendering/transferring faster than a larger derivative; downloads still use
// full resolution. Falls back to the thumbnail when there's no IIIF base (the mock).
function previewUrl(iiif: string, thumb: string): string {
  if (!iiif) return thumb
  return `${iiif.replace(/\/$/, '')}/full/!800,800/0/default.jpg`
}

// Warm the browser cache with a candidate's preview image so it's usually ready by the
// time Preview is clicked. Deduped across the session; the Image() result is unused.
const prefetched = new Set<string>()
function prefetchPreview(c: Candidate) {
  const url = previewUrl(c.iiif, c.thumb)
  if (!url || prefetched.has(url)) return
  prefetched.add(url)
  const img = new Image()
  img.src = url
}

export default function App() {
  return (
    <Toast.Provider>
      <WallpaperApp />
      <Toast.Portal>
        <Toast.Viewport className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
          <ToastList />
        </Toast.Viewport>
      </Toast.Portal>
    </Toast.Provider>
  )
}

function ToastList() {
  const { toasts } = Toast.useToastManager()
  return toasts.map((toast) => (
    <Toast.Root
      key={toast.id}
      toast={toast}
      className="rounded-lg border border-neutral-200 bg-white p-3 shadow-lg data-[ending-style]:opacity-0 data-[starting-style]:opacity-0"
    >
      <Toast.Content>
        <Toast.Title className="text-sm font-semibold" />
        <Toast.Description className="text-xs text-neutral-500" />
      </Toast.Content>
      <Toast.Close
        aria-label="Dismiss"
        className="absolute right-2 top-2 text-neutral-400 hover:text-neutral-700"
      >
        ✕
      </Toast.Close>
    </Toast.Root>
  ))
}

/** A labelled Base UI Number Field with stepper buttons. */
function NumField({
  label,
  value,
  onChange,
  min = 0,
  step = 1,
}: {
  label: string
  value: number
  onChange: (n: number) => void
  min?: number
  step?: number
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className={labelCls}>
        {label}
      </label>
      <NumberField.Root
        id={id}
        value={value}
        onValueChange={(v) => {
          if (v != null) onChange(v)
        }}
        min={min}
        step={step}
      >
        <NumberField.Group className="flex h-9 items-stretch overflow-hidden rounded-md border border-neutral-300 bg-white focus-within:border-neutral-900">
          <NumberField.Decrement className="select-none px-2 text-neutral-500 hover:bg-neutral-100">
            −
          </NumberField.Decrement>
          <NumberField.Input className="w-full min-w-0 border-x border-neutral-200 text-center text-sm outline-none" />
          <NumberField.Increment className="select-none px-2 text-neutral-500 hover:bg-neutral-100">
            +
          </NumberField.Increment>
        </NumberField.Group>
      </NumberField.Root>
    </div>
  )
}

/**
 * Comma-separated subject-term input with autocomplete drawn from the collection's
 * theme vocabulary. The token being typed is whatever follows the last comma; picking
 * a suggestion replaces just that token and keeps any earlier terms.
 */
function SubjectAutocomplete({
  value,
  onValueChange,
  terms,
  subjects,
  onSubmit,
}: {
  value: string
  onValueChange: (v: string) => void
  terms: string[]
  subjects: string[]
  onSubmit?: () => void
}) {
  const activeToken = value.slice(value.lastIndexOf(',') + 1).trim().toLowerCase()
  const chosen = useMemo(() => new Set(terms.map((t) => t.toLowerCase())), [terms])
  const suggestions = useMemo(
    () =>
      subjects
        .filter((s) => !chosen.has(s.toLowerCase()))
        .filter((s) => !activeToken || s.toLowerCase().includes(activeToken))
        .slice(0, 8),
    [subjects, chosen, activeToken],
  )

  function pick(subject: string) {
    const head = value.includes(',') ? `${value.slice(0, value.lastIndexOf(',') + 1)} ` : ''
    onValueChange(`${head}${subject}, `)
  }

  return (
    <Autocomplete.Root
      mode="none"
      value={value}
      items={suggestions}
      onValueChange={(v, details) => {
        if (details.reason === 'item-press') pick(v)
        else onValueChange(v)
      }}
    >
      <Autocomplete.Input
        placeholder="mountain, seascape, forest…"
        // Enter triggers the search — unless a suggestion is highlighted, in which case
        // the autocomplete consumes Enter to pick it (it calls preventDefault).
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.nativeEvent.isComposing && !e.defaultPrevented) {
            onSubmit?.()
          }
        }}
        className="h-9 w-full rounded-md border border-neutral-300 bg-white px-3 text-sm outline-none focus:border-neutral-900"
      />
      <Autocomplete.Portal>
        <Autocomplete.Positioner sideOffset={4} className="z-50">
          <Autocomplete.Popup className="max-h-56 min-w-[12rem] w-[var(--anchor-width)] overflow-auto rounded-md border border-neutral-200 bg-white py-1 text-sm shadow-lg outline-none">
            <Autocomplete.List>
              {suggestions.map((s) => (
                <Autocomplete.Item
                  key={s}
                  value={s}
                  className="cursor-pointer px-3 py-1.5 outline-none data-[highlighted]:bg-neutral-100"
                >
                  {s}
                </Autocomplete.Item>
              ))}
            </Autocomplete.List>
          </Autocomplete.Popup>
        </Autocomplete.Positioner>
      </Autocomplete.Portal>
    </Autocomplete.Root>
  )
}

/** NGA-style "Artwork Type" facet: a popover of checkboxes over the available classifications. */
function ArtworkTypeFilter({
  options,
  value,
  onValueChange,
}: {
  options: { value: string; count: number }[]
  value: string[]
  onValueChange: (v: string[]) => void
}) {
  const label = value.length ? `Artwork Type · ${value.length}` : 'Artwork Type · all'
  return (
    <Popover.Root>
      <Popover.Trigger className="flex h-9 w-full items-center justify-between gap-2 rounded-md border border-neutral-300 bg-white px-3 text-sm outline-none focus:border-neutral-900 data-[popup-open]:border-neutral-900">
        <span className="truncate">{label}</span>
        <span className="text-xs text-neutral-400">▼</span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={4} className="z-50">
          <Popover.Popup className="max-h-72 w-64 overflow-auto rounded-md border border-neutral-200 bg-white p-2 shadow-lg outline-none">
            <div className="flex items-center justify-between px-1 pb-1">
              <span className="text-xs font-medium text-neutral-600">Artwork Type</span>
              {value.length > 0 && (
                <button
                  type="button"
                  className="text-[11px] text-neutral-500 hover:text-neutral-900"
                  onClick={() => onValueChange([])}
                >
                  Clear
                </button>
              )}
            </div>
            <CheckboxGroup value={value} onValueChange={onValueChange} className="flex flex-col">
              {options.map((o) => (
                <label
                  key={o.value}
                  className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-neutral-100"
                >
                  <Checkbox.Root
                    value={o.value}
                    className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-neutral-300 bg-white data-[checked]:border-neutral-900 data-[checked]:bg-neutral-900"
                  >
                    <Checkbox.Indicator className="text-[10px] text-white">✓</Checkbox.Indicator>
                  </Checkbox.Root>
                  <span className="flex-1 truncate">{o.value}</span>
                  <span className="text-[11px] text-neutral-400">{o.count.toLocaleString()}</span>
                </label>
              ))}
            </CheckboxGroup>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}

/** Centered modal showing an enlarged image + NGA-style details for one candidate. */
function PreviewModal({
  candidate,
  onClose,
  onDownload,
  downloading,
}: {
  candidate: Candidate | null
  onClose: () => void
  onDownload: () => void
  downloading: boolean
}) {
  const [loaded, setLoaded] = useState(false)
  useEffect(() => setLoaded(false), [candidate?.id])
  return (
    <Dialog.Root open={!!candidate} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/50 transition-opacity data-[ending-style]:opacity-0 data-[starting-style]:opacity-0" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] w-[min(48rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl bg-white shadow-2xl outline-none transition-opacity data-[ending-style]:opacity-0 data-[starting-style]:opacity-0">
          {candidate && (
            <>
              <div className="relative flex min-h-[240px] items-center justify-center overflow-hidden bg-neutral-100">
                <div
                  aria-hidden
                  className={`absolute inset-0 scale-110 bg-cover bg-center blur-xl transition-opacity duration-300 ${loaded ? 'opacity-0' : 'opacity-60 animate-pulse'}`}
                  style={{ backgroundImage: `url(${candidate.thumb})` }}
                />
                <img
                  key={candidate.id}
                  src={previewUrl(candidate.iiif, candidate.thumb)}
                  alt={candidate.title}
                  onLoad={() => setLoaded(true)}
                  className={`relative z-10 max-h-[70vh] w-auto object-contain transition-opacity duration-300 ${loaded ? 'opacity-100' : 'opacity-0'}`}
                />
              </div>
              <div className="flex flex-col gap-1 p-4">
                <Dialog.Title className="text-xl font-semibold leading-tight">
                  {candidate.title}
                </Dialog.Title>
                {candidate.artist && <span className="text-sm text-neutral-800">{candidate.artist}</span>}
                {candidate.date && <span className="text-sm text-neutral-600">{candidate.date}</span>}
                {(candidate.medium || candidate.accession) && (
                  <span className="text-xs text-neutral-500">
                    {[candidate.medium, candidate.accession].filter(Boolean).join(' · ')}
                  </span>
                )}
                <div className="mt-3 flex items-center gap-2">
                  <button type="button" className={btnPrimary} onClick={onDownload} disabled={downloading}>
                    <span aria-hidden className="mr-1">⤓</span>
                    {downloading ? 'Downloading…' : 'Download'}
                  </button>
                  <Dialog.Close className={btnGhost}>Close</Dialog.Close>
                </div>
              </div>
              <Dialog.Close
                aria-label="Close"
                className="absolute right-2 top-2 flex h-8 w-8 items-center justify-center rounded-full bg-white/90 text-neutral-500 shadow hover:text-neutral-900"
              >
                ✕
              </Dialog.Close>
            </>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function WallpaperApp() {
  const toast = Toast.useToastManager()

  const [status, setStatus] = useState<Status | null>(null)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [subjectCounts, setSubjectCounts] = useState<SubjectCount[]>([])
  const [terms, setTerms] = useState('')
  const [classifications, setClassifications] = useState<string[]>([])
  const [count, setCount] = useState(20)
  const [resValue, setResValue] = useState('4k')
  const [outDir, setOutDir] = useState('')

  const resolution = RESOLUTIONS.find((r) => r.value === resValue) ?? RESOLUTIONS[2]
  const [noDuplicates, setNoDuplicates] = useState(false)

  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [preview, setPreview] = useState<Candidate | null>(null)
  const [searching, setSearching] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [progress, setProgress] = useState<ProgressData | null>(null)
  const [message, setMessage] = useState('')
  const [isMock, setIsMock] = useState(false)

  // Poll status until the collection data finishes loading.
  useEffect(() => {
    let stop = false
    async function poll() {
      const s = await getStatus()
      if (stop) return
      setStatus(s)
      setTerms((t) => (t ? t : s.defaultTerms.join(', ')))
      setOutDir((d) => (d ? d : s.defaultOutDir))
      if (s.error) {
        toast.add({ title: 'Could not load data', description: s.error })
      } else if (!s.ready) {
        setTimeout(poll, 800)
      }
    }
    poll()
    return () => {
      stop = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Reflect whether we're on the real bridge or the browser mock (resolved once).
  useEffect(() => {
    whenReady().then(setIsMock)
  }, [])

  // Load the filter vocabulary (artwork types + subject terms) once data is ready.
  useEffect(() => {
    if (status?.ready && !facets) getFacets().then(setFacets)
  }, [status?.ready, facets])

  // Recompute subject-tag counts whenever the structural filters change. Counts are
  // independent of the typed term, so they don't recompute per keystroke.
  useEffect(() => {
    if (!status?.ready) return
    getSubjectCounts({
      classifications,
      width: resolution.w,
      height: resolution.h,
      noDuplicates,
    }).then(setSubjectCounts)
  }, [status?.ready, classifications, resolution.w, resolution.h, noDuplicates])

  // Wire the Python progress callback.
  useEffect(() => onProgress((p) => setProgress(p)), [])

  const parsedTerms = useMemo(
    () => terms.split(',').map((t) => t.trim()).filter(Boolean),
    [terms],
  )
  const busy = searching || downloading || !status?.ready

  async function doFind(overrideTerms?: string[]): Promise<Candidate[]> {
    const useTerms = overrideTerms ?? parsedTerms
    setSearching(true)
    setMessage('Searching…')
    try {
      const results = await find({
        terms: useTerms,
        classifications,
        width: resolution.w,
        height: resolution.h,
        noDuplicates,
        previewLimit: 60,
      })
      setCandidates(results)
      setSelected(new Set())
      setMessage(
        results.length
          ? `${results.length} ${useTerms.length ? 'match(es)' : 'work(s)'}. Pick images or use “Surprise me”.`
          : 'No results. Try lowering the resolution or changing filters.',
      )
      return results
    } catch (e) {
      toast.add({ title: 'Search failed', description: String(e) })
      return []
    } finally {
      setSearching(false)
    }
  }

  // Toggle a subject tag into the search box and re-run the search immediately.
  function toggleTagAndSearch(tag: string) {
    const has = parsedTerms.some((t) => t.toLowerCase() === tag.toLowerCase())
    const next = has
      ? parsedTerms.filter((t) => t.toLowerCase() !== tag.toLowerCase())
      : [...parsedTerms, tag]
    setTerms(next.join(', '))
    void doFind(next)
  }

  async function doSurprise() {
    let pool = candidates
    if (pool.length === 0) pool = await doFind()
    if (pool.length === 0) return
    const n = Math.min(count, pool.length)
    const picks = [...pool].sort(() => Math.random() - 0.5).slice(0, n)
    setSelected(new Set(picks.map((c) => c.id)))
    setMessage(`Randomly selected ${n} of ${pool.length}.`)
  }

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function downloadItems(items: Candidate[]) {
    if (items.length === 0) {
      setMessage('Select at least one image first.')
      return
    }
    setDownloading(true)
    setProgress({ current: 0, total: items.length, label: '' })
    setMessage(`Downloading ${items.length} wallpaper(s)…`)
    try {
      const res = await download({ items, outDir, width: resolution.w, height: resolution.h })
      setMessage(`Done. Saved ${res.ok}/${res.total} to ${res.outDir}`)
      toast.add({
        title: 'Download complete',
        description: `Saved ${res.ok}/${res.total} wallpaper(s).`,
        timeout: 5000,
      })
    } catch (e) {
      toast.add({ title: 'Download failed', description: String(e) })
    } finally {
      setDownloading(false)
      setProgress(null)
    }
  }

  function doDownload() {
    return downloadItems(candidates.filter((c) => selected.has(c.id)))
  }

  async function doBrowse() {
    const dir = await chooseFolder()
    if (dir) setOutDir(dir)
  }

  const pct = progress && progress.total ? (progress.current / progress.total) * 100 : 0

  return (
    <div className="flex h-full flex-col bg-neutral-50 text-neutral-900">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-neutral-200 bg-white px-5 py-3">
        <h1 className="text-base font-semibold">NGA Wallpaper Browser</h1>
        <div className="flex items-center gap-2 text-xs">
          {isMock && (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">browser mock</span>
          )}
          <span
            className={`rounded-full px-2 py-0.5 ${
              status?.error
                ? 'bg-red-100 text-red-700'
                : status?.ready
                  ? 'bg-green-100 text-green-700'
                  : 'bg-neutral-100 text-neutral-500'
            }`}
          >
            {status?.error
              ? 'error'
              : status?.ready
                ? `ready · ${status.count.toLocaleString()} images`
                : 'loading data…'}
          </span>
        </div>
      </header>

      {/* Controls */}
      <section className="grid grid-cols-2 gap-3 border-b border-neutral-200 bg-white px-5 py-4 sm:grid-cols-3 lg:grid-cols-6">
        <Field.Root className="col-span-full flex flex-col gap-1 lg:col-span-4">
          <Field.Label className={labelCls}>Subject terms (comma-separated)</Field.Label>
          <SubjectAutocomplete
            value={terms}
            onValueChange={setTerms}
            terms={parsedTerms}
            subjects={facets?.subjects ?? []}
            onSubmit={() => {
              if (!busy) void doFind()
            }}
          />
          <span className="text-[11px] text-neutral-400">
            Only open-access works that can be downloaded at your chosen resolution are shown.
          </span>
          {subjectCounts.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {subjectCounts.map((s) => {
                const active = parsedTerms.some((t) => t.toLowerCase() === s.term.toLowerCase())
                return (
                  <button
                    key={s.term}
                    type="button"
                    disabled={busy}
                    onClick={() => toggleTagAndSearch(s.term)}
                    className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors disabled:opacity-40 ${
                      active
                        ? 'border-neutral-900 bg-neutral-900 text-white'
                        : 'border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-100'
                    }`}
                  >
                    {s.term}{' '}
                    <span className={active ? 'text-neutral-300' : 'text-neutral-400'}>
                      ({s.count.toLocaleString()})
                    </span>
                  </button>
                )
              })}
            </div>
          )}
        </Field.Root>

        <NumField label="Count" value={count} onChange={setCount} min={1} />

        <div className="flex flex-col gap-1">
          <label className={labelCls}>Resolution</label>
          <Select.Root
            value={resValue}
            onValueChange={(v) => v && setResValue(v)}
            items={RES_ITEMS}
          >
            <Select.Trigger className="flex h-9 items-center justify-between gap-2 rounded-md border border-neutral-300 bg-white px-3 text-sm outline-none focus:border-neutral-900 data-[popup-open]:border-neutral-900">
              <Select.Value />
              <Select.Icon className="text-xs text-neutral-400">▼</Select.Icon>
            </Select.Trigger>
            <Select.Portal>
              <Select.Positioner sideOffset={4} className="z-50">
                <Select.Popup className="rounded-md border border-neutral-200 bg-white py-1 shadow-lg outline-none">
                  <Select.List>
                    {RESOLUTIONS.map((r) => (
                      <Select.Item
                        key={r.value}
                        value={r.value}
                        className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm outline-none data-[highlighted]:bg-neutral-100"
                      >
                        <Select.ItemIndicator className="w-3 text-neutral-900">✓</Select.ItemIndicator>
                        <Select.ItemText>{r.label}</Select.ItemText>
                      </Select.Item>
                    ))}
                  </Select.List>
                </Select.Popup>
              </Select.Positioner>
            </Select.Portal>
          </Select.Root>
        </div>

        <div className="col-span-full flex flex-col gap-1 sm:col-span-1 lg:col-span-2">
          <label className={labelCls}>Artwork type</label>
          <ArtworkTypeFilter
            options={facets?.classifications ?? []}
            value={classifications}
            onValueChange={setClassifications}
          />
        </div>

        <div className="col-span-full flex flex-col gap-1 sm:col-span-2 lg:col-span-2">
          <label className={labelCls}>Save to</label>
          <div className="flex gap-2">
            <input
              className="h-9 flex-1 rounded-md border border-neutral-300 bg-white px-3 text-sm outline-none focus:border-neutral-900"
              value={outDir}
              onChange={(e) => setOutDir(e.target.value)}
            />
            <button className={btnGhost} onClick={doBrowse} type="button">
              Browse…
            </button>
          </div>
        </div>

        <div className="col-span-full flex items-center justify-between gap-3 self-end sm:col-span-1 lg:col-span-2">
          <label className="flex cursor-pointer items-center gap-2 whitespace-nowrap text-sm">
            <Switch.Root
              checked={noDuplicates}
              onCheckedChange={setNoDuplicates}
              className="relative flex h-5 w-9 shrink-0 items-center rounded-full bg-neutral-300 px-0.5 transition-colors data-[checked]:bg-neutral-900"
            >
              <Switch.Thumb className="h-4 w-4 rounded-full bg-white shadow transition-transform data-[checked]:translate-x-4" />
            </Switch.Root>
            No duplicates
          </label>
          <div className="flex gap-2">
            <button className={btnPrimary} onClick={() => doFind()} disabled={busy} type="button">
              {searching ? 'Searching…' : 'Find matches'}
            </button>
            <button className={btnGhost} onClick={doSurprise} disabled={busy} type="button">
              Surprise me
            </button>
          </div>
        </div>
      </section>

      {/* Results grid (Base UI Scroll Area) */}
      <ScrollArea.Root className="min-h-0 flex-1">
        <ScrollArea.Viewport className="h-full px-5 py-4">
          <ScrollArea.Content>
            {candidates.length === 0 ? (
              <div className="flex h-40 items-center justify-center text-sm text-neutral-400">
                {status?.ready ? 'No results yet — click “Find matches”.' : 'Loading collection…'}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                {candidates.map((c) => {
                  const isSel = selected.has(c.id)
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => toggle(c.id)}
                      onMouseEnter={() => prefetchPreview(c)}
                      className={`group flex flex-col overflow-hidden rounded-lg border bg-white text-left transition-shadow hover:shadow-md ${
                        isSel ? 'border-neutral-900 ring-2 ring-neutral-900' : 'border-neutral-200'
                      }`}
                    >
                      <div className="relative aspect-video bg-neutral-100">
                        <img src={c.thumb} alt={c.title} loading="lazy" className="h-full w-full object-cover" />
                        <span className="absolute right-2 top-2">
                          <Checkbox.Root
                            checked={isSel}
                            // selection is driven by the card button; keep the box in sync visually
                            onCheckedChange={() => toggle(c.id)}
                            onClick={(e) => e.stopPropagation()}
                            className="flex h-5 w-5 items-center justify-center rounded border border-neutral-300 bg-white/90 data-[checked]:border-neutral-900 data-[checked]:bg-neutral-900"
                          >
                            <Checkbox.Indicator className="text-xs text-white">✓</Checkbox.Indicator>
                          </Checkbox.Root>
                        </span>
                        <button
                          type="button"
                          aria-label="Preview"
                          // opening the preview must not toggle the card's selection
                          onClick={(e) => {
                            e.stopPropagation()
                            setPreview(c)
                          }}
                          className="absolute bottom-2 right-2 flex items-center gap-1 rounded-md bg-white/90 px-2 py-1 text-[11px] font-medium text-neutral-700 opacity-0 shadow-sm transition-opacity hover:bg-white group-hover:opacity-100 focus-visible:opacity-100"
                        >
                          <span aria-hidden>⤢</span> Preview
                        </button>
                      </div>
                      <div className="flex flex-col gap-0.5 p-2">
                        <span className="truncate text-xs font-semibold">{c.title}</span>
                        <span className="truncate text-[11px] text-neutral-500">
                          {c.artistLast} · {c.date}
                        </span>
                        <span className="text-[11px] text-neutral-400">
                          {c.srcW}×{c.srcH}
                        </span>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </ScrollArea.Content>
        </ScrollArea.Viewport>
        <ScrollArea.Scrollbar
          orientation="vertical"
          className="m-1 flex w-2 justify-center rounded bg-neutral-100 opacity-0 transition-opacity data-[hovering]:opacity-100 data-[scrolling]:opacity-100"
        >
          <ScrollArea.Thumb className="w-full rounded bg-neutral-400" />
        </ScrollArea.Scrollbar>
      </ScrollArea.Root>

      {/* Footer */}
      <footer className="flex items-center gap-4 border-t border-neutral-200 bg-white px-5 py-3">
        <div className="flex-1">
          {progress ? (
            <Progress.Root value={progress.current} max={progress.total} className="flex items-center gap-3">
              <Progress.Track className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-200">
                <Progress.Indicator
                  className="h-full bg-neutral-900 transition-all"
                  style={{ width: `${pct}%` }}
                />
              </Progress.Track>
              <span className="w-44 truncate text-xs text-neutral-500">
                {progress.current}/{progress.total} {progress.label}
              </span>
            </Progress.Root>
          ) : (
            <span className="text-xs text-neutral-500">{message}</span>
          )}
        </div>
        <span className="text-xs text-neutral-400">{selected.size} selected</span>
        <button className={btnGhost} onClick={() => openFolder(outDir)} type="button">
          Open folder
        </button>
        <button className={btnPrimary} onClick={doDownload} disabled={busy || selected.size === 0} type="button">
          Download selected
        </button>
      </footer>

      <PreviewModal
        candidate={preview}
        onClose={() => setPreview(null)}
        onDownload={() => preview && downloadItems([preview])}
        downloading={downloading}
      />
    </div>
  )
}
