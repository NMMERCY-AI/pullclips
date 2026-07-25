import Navbar from "./components/Navbar"
import PullTab from "./pages/Pull_old_Tab"

export default function App() {
  return (
    <div className="min-h-screen relative">
      <div className="blob1"/><div className="blob2"/><div className="blob3"/>
      <Navbar />
      <main className="relative z-10 max-w-2xl mx-auto px-6 py-12">
        <PullTab />
      </main>
    </div>
  )
}