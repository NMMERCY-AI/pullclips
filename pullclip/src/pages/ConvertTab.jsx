import { useState } from "react"

const API = "http://localhost:8000"
const VIDEO = ["MP4","WEBM","MKV","MOV","AVI","FLV"]
const AUDIO = ["MP3","M4A","WAV","OGG","AAC","FLAC"]

const Spinner = () => (
  <svg className="spinner w-4 h-4 inline mr-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
  </svg>
)

export default function ConvertTab() {
  const [file,setFile]=useState(null)
  const [format,setFormat]=useState("MP4")
  const [group,setGroup]=useState("video")
  const [dragging,setDragging]=useState(false)
  const [loading,setLoading]=useState(false)
  const [result,setResult]=useState(null)
  const [error,setError]=useState(null)
  const [progress,setProgress]=useState(0)
  const [status,setStatus]=useState("")
  const [logs,setLogs]=useState([])

  function addLog(message, type="info") {
    const timestamp = new Date().toLocaleTimeString()
    console.log(`[${timestamp}] ${message}`)
    setLogs(prev => [...prev, {message, type, timestamp}])
  }

  function handleFile(f){ if(f){setFile(f);setResult(null);setError(null);setLogs([]);setProgress(0)} }
  function handleDrop(e){ e.preventDefault();setDragging(false);handleFile(e.dataTransfer.files[0]) }

  async function pollProgress(conversionId) {
    try {
      const res = await fetch(`${API}/convert-progress/${conversionId}`)
      const data = await res.json()
      setProgress(data.percentage)
      setStatus(data.status)
      addLog(`📊 Progress: ${data.percentage}% - ${data.status}`, "progress")
      
      if (data.status === "completed" || data.status === "failed") {
        return data.status === "completed"
      }
      
      // Poll again in 1000ms (increased for large files)
      await new Promise(r => setTimeout(r, 1000))
      return await pollProgress(conversionId)
    } catch (e) {
      addLog(`❌ Progress check failed: ${e.message}`, "error")
      return false
    }
  }

  async function convert(){
    if(!file)return
    setLoading(true)
    setResult(null)
    setError(null)
    setLogs([])
    setProgress(0)
    
    const fileSizeMB = (file.size/1024/1024).toFixed(2)
    addLog(`🚀 Starting conversion: ${file.name} → ${format}`, "start")
    addLog(`   File size: ${fileSizeMB} MB`, "debug")
    
    if (fileSizeMB > 1000) {
      addLog(`⚠️ Large file detected (${fileSizeMB} MB) - this may take 5-30+ minutes`, "warning")
    }
    
    try{
      addLog(`📤 Uploading file to backend...`, "info")
      addLog(`   Backend API: ${API}/convert`, "debug")
      
      const form=new FormData()
      form.append("file",file)
      
      const convertUrl = `${API}/convert?output_format=${format.toLowerCase()}`
      addLog(`📍 POST ${convertUrl}`, "debug")
      addLog(`⏳ Waiting for backend response...`, "info")
      
      const res=await fetch(convertUrl,{method:"POST",body:form})
      const data=await res.json()
      
      if(!res.ok) {
        addLog(`❌ Backend error: ${data.detail||"Failed"}`, "error")
        throw new Error(data.detail||"Failed")
      }
      
      addLog(`✅ Backend response received`, "success")
      addLog(`   Conversion ID: ${data.conversion_id}`, "debug")
      addLog(`   Output file: ${data.filename}`, "debug")
      
      // Start polling progress
      addLog(`🔄 Polling backend for progress (updating every 1s)...`, "info")
      const success = await pollProgress(data.conversion_id)
      
      if(success) {
        addLog(`🎉 Conversion completed successfully!`, "success")
        setResult(data)
      } else {
        addLog(`⚠️ Conversion did not complete successfully`, "warning")
        setError("Conversion failed")
      }
    }catch(e){
      addLog(`💥 Error: ${e.message}`, "error")
      setError(e.message)
    }
    finally{setLoading(false)}
  }

  function saveFile(){
    addLog(`💾 Downloading file: ${result.suggested_name||result.filename}`, "info")
    const a=document.createElement("a")
    a.href=`${API}${result.download_url}`
    a.download=result.suggested_name||result.filename
    a.click()
  }

  const formats = group==="video"?VIDEO:AUDIO

  return (
    <div className="animate-slide-up">
      <div className="mb-8">
        <h1 className="font-syne font-bold text-3xl tracking-tight mb-1 text-gray-800">
          Convert <span style={{background:"linear-gradient(135deg,#6c63ff,#a855f7)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>anything</span>
        </h1>
        <p className="text-sm text-gray-400 font-light">Change any file to any format you need</p>
      </div>

      <div className="glass rounded-2xl p-7 mb-4">
        <span className="label">Input file</span>
        <div onDragOver={e=>{e.preventDefault();setDragging(true)}} onDragLeave={()=>setDragging(false)} onDrop={handleDrop}
          onClick={()=>document.getElementById("conv-input").click()}
          className={`drop-zone rounded-xl p-10 text-center ${dragging?"dragging":""} ${file?"has-file":""}`}>
          {file?(
            <div>
              <div className="font-medium text-sm mb-1 text-purple-600">{file.name}</div>
              <div className="text-xs text-gray-400">{(file.size/1024/1024).toFixed(1)} MB — click to change</div>
            </div>
          ):(
            <div>
              <div className="text-3xl mb-3 text-gray-300">↑</div>
              <div className="text-sm text-gray-500 mb-1">Drop any file here</div>
              <div className="text-xs text-gray-400">Video, audio, any format</div>
            </div>
          )}
          <input id="conv-input" type="file" className="hidden" onChange={e=>handleFile(e.target.files[0])}/>
        </div>
      </div>

      <div className="glass rounded-2xl p-7 mb-4">
        <span className="label">Convert to</span>
        <div className="flex gap-2 mb-4">
          {["video","audio"].map(g=>(
            <button key={g} onClick={()=>{setGroup(g);setFormat(g==="video"?"MP4":"MP3")}}
              className="px-4 py-2 rounded-lg text-xs font-medium border transition-all cursor-pointer"
              style={{
                borderColor:group===g?"#6c63ff":"rgba(0,0,0,0.08)",
                color:group===g?"#6c63ff":"#9ca3af",
                background:group===g?"rgba(108,99,255,0.07)":"rgba(255,255,255,0.5)"
              }}>
              {g.charAt(0).toUpperCase()+g.slice(1)}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-3">
          {formats.map(f=>(
            <button key={f} onClick={()=>setFormat(f)} className={`format-card p-3 rounded-xl text-center ${format===f?"selected":""}`}>
              <span className="font-syne font-bold text-sm" style={{color:format===f?"#6c63ff":"#374151"}}>{f}</span>
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="glass rounded-2xl p-7 mb-4">
          <div className="flex items-center justify-between mb-3">
            <span className="label">Conversion Progress</span>
            <span className="text-2xl font-bold text-purple-600">{progress}%</span>
          </div>
          <div style={{background:"rgba(0,0,0,0.05)",height:"8px",borderRadius:"4px",overflow:"hidden"}}>
            <div style={{width:`${progress}%`,height:"100%",background:"linear-gradient(90deg, #6c63ff, #a855f7)",transition:"width 0.3s"}}></div>
          </div>
          <p className="text-xs text-gray-500 mt-3 capitalize">{status}</p>
        </div>
      )}

      <button onClick={convert} disabled={!file||loading}
        className="w-full py-4 rounded-xl text-base font-semibold cursor-pointer border-none transition-all duration-200"
        style={{background:file?"linear-gradient(135deg,#6c63ff,#a855f7)":"rgba(0,0,0,0.06)",color:file?"white":"#9ca3af"}}>
        {loading?<><Spinner/>Converting...</>:`Convert to ${format}`}
      </button>

      {error&&<div className="flex items-center gap-3 p-4 rounded-xl border text-sm mt-4 animate-slide-up" style={{background:"rgba(239,68,68,0.05)",borderColor:"rgba(239,68,68,0.2)",color:"#ef4444"}}><span>✕</span>{error}</div>}
      {result&&(
        <div className="flex items-center gap-3 p-4 rounded-xl border text-sm mt-4 animate-slide-up" style={{background:"rgba(34,197,94,0.05)",borderColor:"rgba(34,197,94,0.2)",color:"#16a34a"}}>
          <span>✓</span> Converted to {format} successfully
          <button onClick={saveFile} className="ml-auto btn-primary px-4 py-2 rounded-xl text-xs font-medium cursor-pointer">Save file</button>
        </div>
      )}

      {logs.length > 0 && (
        <div className="glass rounded-2xl p-7 mt-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">📋 Connection Log</h3>
          <div style={{maxHeight:"250px",overflowY:"auto",fontFamily:"monospace",fontSize:"11px"}}>
            {logs.map((log, i) => (
              <div key={i} className="mb-1" style={{color:log.type==="error"?"#ef4444":log.type==="success"?"#16a34a":log.type==="debug"?"#9ca3af":"#374151"}}>
                <span style={{color:"#9ca3af"}}>[{log.timestamp}]</span> {log.message}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
