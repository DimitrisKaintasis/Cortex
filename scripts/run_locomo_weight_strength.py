"""Offline development-only counterfactual; disposable aggregates, not ledger replay."""
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from scripts.run_locomo_learning import (
    ROOT, CONFIG, ImportedLongMemEvalCase, LongMemEvalPipelineRunner,
    OfflineEmbedder, RetrievalChannels, SQLiteRepository, aggregate,
    copy_database, digest, write_json,
)


def amplified(initial, learned, multiplier):
    if initial == learned:
        return learned
    return min(10.0, max(0.0, initial + multiplier * (learned - initial)))


def main():
    source = ROOT / 'scoring-20260905T201307Z'
    output = ROOT / ('strength-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
    output.mkdir()
    config = json.loads(CONFIG.read_text())
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    history = next(h for h in manifest['histories'] if h['sample_id'] == 'conv-26')
    assert history['sample_id'] in config['development']
    reference = json.loads((source / 'conv-26.json').read_text())
    frozen_path = source / 'conv-26-frozen.sqlite3'
    learned_path = source / 'conv-26-query_evidence.sqlite3'
    hashes = {str(p): digest(p) for p in (frozen_path, learned_path)}
    report = {'scope': 'development only; counterfactual aggregate scaling, not maturity',
              'ledger_note': 'Disposable SQL weight overrides do not update the event ledger; never deploy these databases.',
              'source_hashes': hashes, 'baseline': aggregate(reference['baseline']), 'branches': {}}
    print('Output: ' + str(output), flush=True)
    for multiplier in (1, 3, 10):
        path = output / f'dev-{multiplier}x.sqlite3'
        copy_database(learned_path, path)
        counts = {}
        with closing(sqlite3.connect(frozen_path.resolve().as_uri() + '?mode=ro', uri=True)) as initial, closing(sqlite3.connect(path)) as target:
            for table, keys in (
                ('atom_tags', ('atom_id', 'tag_id')),
                ('atom_links', ('from_atom_id', 'to_atom_id', 'relation')),
                ('tag_relations', ('source_tag_id', 'target_tag_id', 'relation_type')),
            ):
                columns = ','.join(keys)
                old = {tuple(r[:-1]): r[-1] for r in initial.execute(f'SELECT {columns},weight_raw FROM {table}')}
                rows = list(target.execute(f'SELECT {columns},weight_raw FROM {table}'))
                changed = 0
                for row in rows:
                    key, weight = tuple(row[:-1]), row[-1]
                    new = amplified(old.get(key, 0.0), weight, multiplier)
                    if new != weight:
                        target.execute(f'UPDATE {table} SET weight_raw=? WHERE ' + ' AND '.join(k+'=?' for k in keys), (new, *key))
                        changed += 1
                counts[table] = changed
            target.commit()
        repository = SQLiteRepository(path)
        try:
            namespace = 'locomo-learning-v1:conv-26'
            atoms = [a for batch in repository.iter_atoms(namespace=namespace) for a in batch]
            sources = {a.metadata['dia_id']: a for a in atoms if 'dia_id' in a.metadata}
            dates = [a.occurred_at for a in sources.values()]
            documents = tuple(sorted({a.document_id for a in sources.values()}))
            runner = LongMemEvalPipelineRunner(repository, embedder=OfflineEmbedder(config['embedding_model']))
            rows = []
            for q in history['evaluation']:
                feature = json.loads((ROOT / 'query-cache' / (q['id'] + '.json')).read_text())
                case = ImportedLongMemEvalCase(q['id'], str(q['category']), namespace, q['query'], '', max(dates), (), tuple(sources[e].atom_id for e in q['evidence']), documents, min(dates), max(dates), len(documents), len(sources))
                row = runner._evaluate_case(case, top_k=config['top_k'], retrieval_channels=RetrievalChannels(temporal=False, temporal_summaries=False), query_text=q['query'], query_tags=tuple(feature['tags']), query_vector=tuple(feature['vector']))
                if row['retrieval_diagnostics'].get('warnings'):
                    raise ValueError('Retrieval warnings require inspection')
                rows.append({'id': q['id'], 'group': q['group'], **row})
                print(f'{multiplier}x {len(rows)}/{len(history["evaluation"])}', flush=True)
        finally:
            repository.close()
        if multiplier == 1:
            expected = reference['branches']['query_evidence']['rounds'][-1]['rows']
            if [r['retrieved_atom_ids'] for r in rows] != [r['retrieved_atom_ids'] for r in expected]:
                raise ValueError('1x reproducibility check failed')
        report['branches'][str(multiplier)] = {'modified_aggregates': counts, 'metrics': aggregate(rows), 'rows': rows}
        write_json(output / f'{multiplier}x.json', report['branches'][str(multiplier)])
    assert all(digest(__import__('pathlib').Path(p)) == h for p, h in hashes.items())
    write_json(output / 'report.json', report)
    print('COMPLETE ' + str(output / 'report.json'), flush=True)


if __name__ == '__main__':
    main()
