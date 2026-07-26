import { useState, useRef, useEffect } from "react"

const API = "https://pullclips.onrender.com"
const PLATFORMS = ["Instagram","TikTok","Twitter","Facebook","Reddit"]

const Spinner = () => (
  <svg className="spinner w-4 h-4 inline mr-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
  </svg>
)

// Simple animated progress bar — we don't get real % from the backend for /pull,
// so this fills smoothly toward ~90% while waiting, then snaps to 100% on success.
function ProgressBar({ active, label }) {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!active) { setWidth(0); return }
    setWidth(0)
    const t = setTimeout(() => setWidth(90), 50)
    return () => clearTimeout(t)
  }, [active])
  if (!active) return null
  return (
    <div className="mb-2">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500">{label}</span>
      </div>
      <div style={{background:"rgba(0,0,0,0.06)",height:"6px",borderRadius:"3px",overflow:"hidden"}}>
        <div style={{width:`${width}%`,height:"100%",background:"linear-gradient(90deg, #6c63ff, #a855f7)",transition:"width 3s ease-out"}}></div>
      </div>
    </div>
  )
}

// Wraps fetch so it never hangs forever — if the backend doesn't respond in time,
// the user gets a clear "try again" message instead of an endless spinner.
async function fetchWithTimeout(url, options, timeoutMs = 70000) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } catch (e) {
    if (e.name === "AbortError") {
      throw new Error("This is taking too long. Please try again in a moment.")
    }
    throw new Error("Couldn't reach the server. Please try again in a moment.")
  } finally {
    clearTimeout(timer)
  }
}

export default function PullTab() {
  const [url,setUrl]=useState("")
  const [checking,setChecking]=useState(false)
  const [videoInfo,setVideoInfo]=useState(null)   // { title, thumbnail, formats: [...] }
  const [selectedFormat,setSelectedFormat]=useState(null)
  const [loading,setLoading]=useState(false)
  const [result,setResult]=useState(null)
  const [error,setError]=useState(null)

  const statusRef = useRef(null)

  // Whenever loading starts, or new content appears, scroll it into view
  // so the user never loses track of what's happening.
  useEffect(() => {
    if ((checking || loading || videoInfo || error || result) && statusRef.current) {
      statusRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  }, [checking, loading, videoInfo, error, result])

  async function checkLink() {
    if(!url.trim()) return
    setChecking(true); setError(null); setResult(null); setVideoInfo(null); setSelectedFormat(null)
    try {
      const res = await fetchWithTimeout(`${API}/check`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url})})
      const data = await res.json()
      if(!res.ok) throw new Error(data.detail||"Could not read this link")
      setVideoInfo(data)
      if(data.formats && data.formats.length>0) setSelectedFormat(data.formats[0])
    } catch(e){ setError(e.message) }
    finally { setChecking(false) }
  }

  async function pull() {
    if(!url.trim() || !selectedFormat) return
    setLoading(true); setResult(null); setError(null)
    try {
      const res = await fetchWithTimeout(`${API}/pull`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
        url,
        format_id: selectedFormat.format_id,
        is_audio: selectedFormat.type === "audio"
      })})
      const data = await res.json()
      if(!res.ok) throw new Error(data.detail||"Failed")
      setResult(data)
    } catch(e){ setError(e.message) }
    finally{ setLoading(false) }
  }

  function saveFile(){
    const a=document.createElement("a"); a.href=`${API}${result.download_url}`; a.download=result.suggested_name||result.filename; a.click()
  }

  function formatSize(bytes){
    if(!bytes) return ""
    const mb = bytes/1024/1024
    return mb > 1024 ? `${(mb/1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`
  }

  function formatDuration(sec){
    if(!sec) return ""
    const m = Math.floor(sec/60)
    const s = Math.floor(sec%60)
    return `${m}:${s.toString().padStart(2,"0")}`
  }

  return (
    <div className="animate-slide-up">
      <div className="mb-8">
        <h1 className="font-syne font-bold text-3xl tracking-tight mb-1 text-gray-800">
          Pull a <span style={{background:"linear-gradient(135deg,#6c63ff,#a855f7)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>clip</span>
        </h1>
        <p className="text-sm text-gray-400 font-light">Paste any social media link and download instantly</p>
      </div>

      <div className="glass rounded-2xl p-7 mb-4">
        <span className="label">Video link</span>
        <div className="flex gap-3">
          <input className="glass-input flex-1 rounded-xl px-4 py-3 text-sm text-gray-700"
            placeholder="https://tiktok.com/@user/video/..."
            value={url} onChange={e=>setUrl(e.target.value)} onKeyDown={e=>e.key==="Enter"&&checkLink()}/>
          <button onClick={checkLink} disabled={checking} className="btn-primary px-5 py-3 rounded-xl text-sm font-medium cursor-pointer whitespace-nowrap">
            {checking?<><Spinner/>Checking...</>:"Check Link"}
          </button>
        </div>
        <div className="flex gap-2 flex-wrap mt-4">
          {PLATFORMS.map(p=>(
            <span key={p} className="text-[11px] px-3 py-1 rounded-full font-medium" style={{background:"rgba(108,99,255,0.07)",color:"#9ca3af",border:"1px solid rgba(108,99,255,0.12)"}}>{p}</span>
          ))}
        </div>
      </div>

      {/* This anchor is where we scroll to, so the loading/result status is always visible */}
      <div ref={statusRef}>
        <ProgressBar active={checking} label="Checking link..." />

        {videoInfo && (
          <div className="glass rounded-2xl p-7 mb-4 animate-slide-up">
            <div className="flex gap-4 mb-5">
              {videoInfo.thumbnail && (
                <img src={videoInfo.thumbnail} alt="" className="w-32 h-20 object-cover rounded-lg flex-shrink-0" style={{border:"1px solid rgba(255,255,255,0.7)"}}/>
              )}
              <div className="min-w-0 flex flex-col justify-center">
                <div className="font-medium text-sm text-gray-800 truncate">{videoInfo.title}</div>
                {videoInfo.duration ? (
                  <div className="text-xs text-gray-400 mt-1">{formatDuration(videoInfo.duration)}</div>
                ) : null}
              </div>
            </div>

            <span className="label">Format & quality</span>
            {videoInfo.formats.length>0 ? (
              <select
                className="glass-input w-full rounded-xl px-4 py-3 text-sm text-gray-700 cursor-pointer"
                value={selectedFormat?.format_id || ""}
                onChange={e=>setSelectedFormat(videoInfo.formats.find(f=>f.format_id===e.target.value))}
              >
                {videoInfo.formats.map(f=>(
                  <option key={f.format_id} value={f.format_id}>
                    {f.type==="audio" ? "Audio" : f.resolution} · {f.ext.toUpperCase()}{f.filesize?` · ${formatSize(f.filesize)}`:""}
                  </option>
                ))}
              </select>
            ) : (
              <p className="text-xs text-gray-400">No downloadable formats found for this link.</p>
            )}
          </div>
        )}

        {videoInfo && videoInfo.formats.length>0 && (
          <>
            <ProgressBar active={loading} label="Downloading..." />
            <button onClick={pull} disabled={!selectedFormat||loading}
              className="w-full py-4 rounded-xl text-base font-semibold cursor-pointer border-none transition-all duration-200 mb-4"
              style={{background:selectedFormat?"linear-gradient(135deg,#6c63ff,#a855f7)":"rgba(0,0,0,0.06)",color:selectedFormat?"white":"#9ca3af"}}>
              {loading?<><Spinner/>Pulling...</>:`Download ${selectedFormat?(selectedFormat.type==="audio"?"Audio":selectedFormat.resolution):""}`}
            </button>
          </>
        )}

        {error&&(
          <div className="flex items-center gap-3 p-4 rounded-xl border text-sm animate-slide-up" style={{background:"rgba(239,68,68,0.05)",borderColor:"rgba(239,68,68,0.2)",color:"#ef4444"}}>
            <span>✕</span>{error}
          </div>
        )}
        {result&&(
          <div className="flex items-center gap-3 p-4 rounded-xl border text-sm animate-slide-up" style={{background:"rgba(34,197,94,0.05)",borderColor:"rgba(34,197,94,0.2)",color:"#16a34a"}}>
            <span>✓</span>
            <div><div className="font-medium">{result.title}</div><div className="text-xs mt-0.5 opacity-60">Ready to save</div></div>
            <button onClick={saveFile} className="ml-auto btn-primary px-4 py-2 rounded-xl text-xs font-medium cursor-pointer">Save file</button>
          </div>
        )}
      </div>
    </div>
  )
}