import Navbar from "./components/Navbar"
import PullTab from "./pages/PullTab"
import InfoSection from "./pages/InfoSection"

export default function App() {
  return (
    <div className="min-h-screen relative flex flex-col">
      <div className="blob1"/><div className="blob2"/><div className="blob3"/>
      <Navbar />
      <main className="relative z-10 max-w-2xl mx-auto px-6 py-12 flex-1 w-full">
        <PullTab />
        <InfoSection />
      </main>
      <footer className="relative z-10 text-center text-xs text-gray-400 py-6 border-t" style={{borderColor:"rgba(0,0,0,0.06)"}}>
        <p>© 2026 PullClip</p>
      </footer>
    </div>
  )
}