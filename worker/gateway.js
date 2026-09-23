/** Shared gateway kept outside the Pages route directory. */
export async function onRequest(context) {
  const incoming = new URL(context.request.url)
  const configured = context.env.REPLAY_UPSTREAM
  if (!configured) return Response.json({message:'리플레이 서비스를 준비하고 있어요.'}, {status:503})
  let upstream
  try { upstream = new URL(configured) } catch {
    return Response.json({message:'리플레이 연결 설정 오류'}, {status:503})
  }
  if (upstream.protocol !== 'https:' || upstream.pathname !== '/' || upstream.search || upstream.hash || upstream.username || upstream.password) {
    return Response.json({message:'리플레이 연결 설정 오류'}, {status:503})
  }
  const target = new URL(incoming.pathname + incoming.search, upstream)
  const headers = new Headers(context.request.headers)
  headers.delete('host')
  const request = new Request(target, {method:context.request.method,headers,
    body:['GET','HEAD'].includes(context.request.method)?undefined:context.request.body,redirect:'manual'})
  try {
    const result = await fetch(request)
    const outgoing = new Headers(result.headers)
    outgoing.set('Cache-Control','no-store')
    outgoing.set('X-Content-Type-Options','nosniff')
    return new Response(result.body,{status:result.status,headers:outgoing})
  } catch {
    return Response.json({message:'리플레이 분석기에 연결하지 못했어요.'},{status:502})
  }
}
