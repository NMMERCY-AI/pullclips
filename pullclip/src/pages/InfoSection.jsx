export default function InfoSection() {
  return (
    <div className="mt-16 max-w-2xl mx-auto px-2 text-gray-600">
      <section className="mb-10">
        <h2 className="font-syne font-bold text-xl text-gray-800 mb-3">About PullClip</h2>
        <p className="text-sm leading-relaxed">
          PullClip is a free online tool that lets you download videos from Instagram, TikTok,
          Twitter/X, Facebook, and Reddit. Just paste a link, pick a quality, and save the video
          straight to your device — no sign-up, no software to install, and no watermarks added.
        </p>
      </section>

      <section className="mb-10">
        <h2 className="font-syne font-bold text-xl text-gray-800 mb-4">How it works</h2>
        <ol className="space-y-3 text-sm">
          <li><strong className="text-gray-800">1. Copy the video link.</strong> Open the video on Instagram, TikTok, Twitter/X, Facebook, or Reddit and copy its URL.</li>
          <li><strong className="text-gray-800">2. Paste it above and click "Check Link."</strong> PullClip reads the video and shows every quality actually available for it.</li>
          <li><strong className="text-gray-800">3. Pick a quality and download.</strong> Choose your preferred resolution or audio-only option, then click download to save the file.</li>
        </ol>
      </section>

      <section>
        <h2 className="font-syne font-bold text-xl text-gray-800 mb-4">Frequently asked questions</h2>
        <div className="space-y-5 text-sm">
          <div>
            <h3 className="font-semibold text-gray-800 mb-1">Is PullClip free to use?</h3>
            <p>Yes, PullClip is completely free with no sign-up required.</p>
          </div>
          <div>
            <h3 className="font-semibold text-gray-800 mb-1">Which platforms does PullClip support?</h3>
            <p>PullClip currently supports Instagram, TikTok, Twitter/X, Facebook, and Reddit video links.</p>
          </div>
          <div>
            <h3 className="font-semibold text-gray-800 mb-1">What video quality can I download?</h3>
            <p>PullClip shows every resolution and format that's actually available for the video you paste, up to 1080p.</p>
          </div>
          <div>
            <h3 className="font-semibold text-gray-800 mb-1">Does PullClip add a watermark to downloads?</h3>
            <p>No, videos are downloaded in their original quality with no watermark added.</p>
          </div>
        </div>
      </section>
    </div>
  )
}