<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Two design systems, sealed at the route boundary

The dashboard surface uses `components/px/` (hand-rolled, on `@base-ui-components/react`).
The candidate interview surface (`app/(interview)/`) uses a shadcn enclave at `components/{ui,agents-ui,ai-elements}/` for LiveKit's Agents UI block. **Dashboard files must not import from that enclave** — `app/globals.css` maps the shadcn token namespace onto the px palette so visual coherence is automatic.
