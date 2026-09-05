import { RazorpayTestImporter } from "@/components/razorpay-test-importer";

export default function ImportsPage() {
  return <div className="shell importsShell"><section className="hero"><div><div className="eyebrow">Integration workspace</div><h1>Bring in real Test Mode failures.</h1><p>Use actual Razorpay sandbox data for the demo, without ever exposing credentials in the browser or triggering a recovery action during import.</p></div><span className="tag">Test Mode only</span></section><RazorpayTestImporter /></div>;
}
