import { useState } from "react"

const API = "http://localhost:8000"
const FORMATS = [{id:"mp3",label:"MP3",desc:"Most compatible"},{id:"m4a",label:"M4A",desc:"High quality"},{id:"wav",label:"WAV",desc:"Lossless"},{id:"ogg",label:"OGG",desc:"Open source"},{id:"aac",label:"AAC",desc:"Apple standard"},{id:"flac",label:"FLAC",desc:"Studio quality"}]

const Spinner = () => (
  <svg className="spinner w-4 h-4 inline mr-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
  </svg>
)

export default function ToAudioTab() {
  const [file,setFile]=useState(null)
  const [format,setFormat]=useState("mp3")
  const [dragging,setDragging]=useState(false)
  const [loading,setLoading]=useState(false)
  const [result,setResult]=useState(null)
  const [error,setError]=useState(null)

  function handleFile(f){ if(f){setFile(f);setResult(null);setError(null)} }
  function handleDrop(e){ e.preventDefault();setDragging(false);handleFile(e.dataTransfer.files[0]) }

  async function convert(){
    if(!file)return; setLoading(true); setResult(null); setError(null)
    try{
      const form=new FormData(); form.append("file",file)
      const res=await fetch(`${API}/to-audio?output_format=${format}`,{method:"POST",body:form})
      const data=await res.json()
      if(!res.ok)throw new Error(data.detail||"Failed")
      setResult(data)
    }catch(e){setError(e.message)}
    finally{setLoading(false)}
  }

  function saveFile(){
    const a=document.createElement("a"); a.href=`${API}${result.download_url}`; a.download=result.suggested_name||result.filename; a.click()
  }

  return (
    <div className="animate-slide-up">
      <div className="mb-8">
        <h1 className="font-syne font-bold text-3xl tracking-tight mb-1 text-gray-800">
          Video to <span style={{background:"linear-gradient(135deg,#6c63ff,#a855f7)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>audio</span>
        </h1>
        <p className="text-sm text-gray-400 font-light">Extract audio from any video file</p>
      </div>

      <div className="glass rounded-2xl p-7 mb-4">
        <span className="label">Video file</span>
        <div onDragOver={e=>{e.preventDefault();setDragging(true)}} onDragLeave={()=>setDragging(false)} onDrop={handleDrop}
          onClick={()=>document.getElementById("audio-input").click()}
          className={`drop-zone rounded-xl p-10 text-center ${dragging?"dragging":""} ${file?"has-file":""}`}>
          {file?(
            <div>
              <div className="font-medium text-sm mb-1 text-purple-600">{file.name}</div>
              <div className="text-xs text-gray-400">{(file.size/1024/1024).toFixed(1)} MB — click to change</div>
            </div>
          ):(
            <div>
              <div className="text-3xl mb-3 text-gray-300">↑</div>
              <div className="text-sm text-gray-500 mb-1">Drop your video here</div>
              <div className="text-xs text-gray-400">MP4, MOV, AVI, WEBM, MKV</div>
            </div>
          )}
          <input id="audio-input" type="file" accept="video/*" className="hidden" onChange={e=>handleFile(e.target.files[0])}/>
        </div>
      </div>

      <div className="glass rounded-2xl p-7 mb-4">
        <span className="label">Output format</span>
        <div className="grid grid-cols-3 gap-3">
          {FORMATS.map(f=>(
            <button key={f.id} onClick={()=>setFormat(f.id)} className={`format-card p-4 rounded-xl text-center ${format===f.id?"selected":""}`}>
              <div className="font-syne font-bold text-base mb-1" style={{color:format===f.id?"#6c63ff":"#374151"}}>{f.label}</div>
              <div className="text-[11px] text-gray-400">{f.desc}</div>
            </button>
          ))}
        </div>
      </div>

      <button onClick={convert} disabled={!file||loading}
        className="w-full py-4 rounded-xl text-base font-semibold cursor-pointer border-none transition-all duration-200"
        style={{background:file?"linear-gradient(135deg,#6c63ff,#a855f7)":"rgba(0,0,0,0.06)",color:file?"white":"#9ca3af"}}>
        {loading?<><Spinner/>Converting...</>:`Convert to ${format.toUpperCase()}`}
      </button>

      {error&&<div className="flex items-center gap-3 p-4 rounded-xl border text-sm mt-4 animate-slide-up" style={{background:"rgba(239,68,68,0.05)",borderColor:"rgba(239,68,68,0.2)",color:"#ef4444"}}><span>✕</span>{error}</div>}
      {result&&(
        <div className="flex items-center gap-3 p-4 rounded-xl border text-sm mt-4 animate-slide-up" style={{background:"rgba(34,197,94,0.05)",borderColor:"rgba(34,197,94,0.2)",color:"#16a34a"}}>
          <span>✓</span> Converted to {format.toUpperCase()} successfully
          <button onClick={saveFile} className="ml-auto btn-primary px-4 py-2 rounded-xl text-xs font-medium cursor-pointer">Save file</button>
        </div>
      )}
    </div>
  )
}
