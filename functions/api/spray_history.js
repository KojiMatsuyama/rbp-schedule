// GET /api/spray_history - 全ての記録を取得
// POST /api/spray_history - 記録を追加・更新
// DELETE /api/spray_history?date=xxx - 記録を削除
export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const db = env.DB;

  try {
    if (request.method === 'GET') {
      const result = await db.prepare('SELECT * FROM spray_history ORDER BY date ASC').all();
      const sprayHistory = result.results || [];
      return Response.json({ success: true, data: sprayHistory });
    }

    if (request.method === 'POST') {
      const body = await request.json();
      const { date, pests, vector } = body;
      if (!date || !pests || !vector) {
        return Response.json({ success: false, error: 'date, pests, vector required' }, { status: 400 });
      }
      await db.prepare(
        'INSERT INTO spray_history (date, pests, vector) VALUES (?, ?, ?) ON CONFLICT(date) DO UPDATE SET pests=?, vector=?'
      ).bind(date, JSON.stringify(pests), JSON.stringify(vector), JSON.stringify(pests), JSON.stringify(vector)).run();
      return Response.json({ success: true });
    }

    if (request.method === 'DELETE') {
      const date = url.searchParams.get('date');
      if (!date) {
        return Response.json({ success: false, error: 'date required' }, { status: 400 });
      }
      await db.prepare('DELETE FROM spray_history WHERE date = ?').bind(date).run();
      return Response.json({ success: true });
    }

    return Response.json({ success: false, error: 'Invalid method' }, { status: 405 });
  } catch (e) {
    return Response.json({ success: false, error: e.message }, { status: 500 });
  }
}
