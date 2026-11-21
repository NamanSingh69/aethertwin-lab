"""Score taped landmark spans against the same spans picked in the twin's mesh (survey-style span accuracy).

    python tools/score_landmarks.py --spans reports/landmarks/spans.csv --points reports/landmarks/points.txt \
        --twin %USERPROFILE%/aethertwin_twin --output reports/landmarks/score.json --markdown reports/landmarks/score.md

Procedure (docs/LAB_DAY.md, "Landmark spans"): each span is taped twice in mm between fixed features at or above the
LiDAR's height; the same two features are then picked in twin_dir/mesh.ply (for example in CloudCompare) and their
coordinates written down. The error of a span is the twin distance minus the mean of the two tapes (positive: the twin
is longer). Span lengths do not depend on the coordinate frame, so any frame works as long as every point of the file
comes from the same mesh in the same frame (in CloudCompare: the same global shift for all picks).

Inputs
  --spans  CSV with a header row: name,from,to,tape1_mm,tape2_mm and an optional note column (other columns are
           ignored). Both tapes are required, in mm, finite and > 0; a bad value stops the tool (exit 2) naming the span
           and the column. Span names must be unique; from and to name points of the points file.
  --points metres, in the twin's coordinates, one of
           * JSON: {"name": [x, y, z], ...};
           * CSV with a header row name,x,y,z (any first-column header name; the last three headers must be x,y,z);
           * CloudCompare "Point list picking" ASCII exports, one point per line, no header:
               label,x,y,z   (export with label names; CloudCompare's default labels are "Point #0", "Point #1", ...),
               index,x,y,z   (export with the point index: the name is the index as written, e.g. "0"),
               x,y,z         (coordinates only: the name is the 0-based line number among the data lines, "0", "1", ...).
           Text files may use comma, semicolon, tab or whitespace separators (the first of , ; tab found on a line;
           otherwise whitespace); a label may itself contain the separator (everything before the last three
           numbers is the label). Empty lines and lines starting with '#' or '//' are skipped. Labelled and
           unlabelled lines cannot be mixed, and a label may appear only once. The CloudCompare layouts follow its
           point-list export menu as remembered by the author (the documentation was not reachable when this was
           written); check one exported file against this list before relying on it.
  --twin   optional twin directory: reconstruction.json and bundle_manifest.json are read (never written) for the
           report's provenance: map revision, camera calibration measured or not, floor fit and declared LiDAR height,
           the build's return-to-start error and the mesh hash recorded in the manifest.

Output: per span tape mean, tape difference (tape2 - tape1; flagged when |difference| > --tape-tolerance-mm, kept but
marked), twin distance, signed and absolute error and relative error (% of the tape mean); summaries (n, bias = mean
signed error, mean |error|, RMSE, max |error|, 68th and 95th percentile of |error| with np.percentile's linear
interpolation, relative error) for all spans, spans without flagged tape pairs, spans over 3 m (tape mean > 3000 mm)
and spans over 3 m without flagged tape pairs. --output writes JSON, --markdown a table ready for the submission.
Exit code 0 when every span is scored, 2 on missing or invalid input or when a span's point is missing (the report is
still written, with the missing points listed and those spans unscored).
"""
from __future__ import annotations
import argparse,csv,hashlib,json,math,sys
from pathlib import Path
import numpy as np

HONESTY=("Span lengths compare the twin's geometry with a tape; picking error in the mesh and tape error are included. "
         "Not a full-map accuracy survey.")
NOTES=['Error = twin distance minus the mean of the two tapes (positive: the twin is longer).',
       'Tape and point-picking uncertainty are not separated from the twin error; the tape difference column shows the tape spread.',
       'Spans at or above LiDAR height only; they say nothing about the parts of the map no span touches.']
SPAN_COLUMNS=('name','from','to','tape1_mm','tape2_mm')
LONG_MM=3000.  # "spans over 3 m": tape mean strictly longer than this
GROSS_PCT=20.  # a span this far off (relative) is more likely a wrong pick or a unit slip than map error


class InputError(ValueError):pass


def sha256(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def number(text,where):
    text=(text or '').strip()
    if not text:raise InputError(f'{where} is empty')
    try:value=float(text)
    except ValueError:raise InputError(f'{where} is not a number: {text!r}') from None
    if not math.isfinite(value):raise InputError(f'{where} is not a finite number: {text!r}')
    return value


def read_spans(path):
    path=Path(path)
    if not path.is_file():raise InputError(f'spans file not found: {path}')
    with path.open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream);header=[(h or '').strip() for h in (reader.fieldnames or [])]
        missing=[c for c in SPAN_COLUMNS if c not in header]
        if missing:raise InputError(f'{path}: missing column(s) {", ".join(missing)}; expected header {",".join(SPAN_COLUMNS)}[,note]')
        reader.fieldnames=header;spans=[];names=set()
        for line,row in enumerate(reader,start=2):
            row={k:(v or '').strip() for k,v in row.items() if k is not None}
            if not any(row.values()):continue
            name=row['name'];where=f"{path.name} line {line} (span {name!r})"
            if not name:raise InputError(f'{path.name} line {line}: span name is empty')
            if name in names:raise InputError(f'{where}: duplicate span name')
            if not row['from'] or not row['to']:raise InputError(f'{where}: from and to must both name a point')
            if row['from']==row['to']:raise InputError(f'{where}: from and to are the same point {row["from"]!r}')
            tapes=[number(row[c],f'{where} {c}') for c in ('tape1_mm','tape2_mm')]
            for c,v in zip(('tape1_mm','tape2_mm'),tapes):
                if v<=0:raise InputError(f'{where} {c} must be a positive length in mm, got {row[c]!r}')
            names.add(name);spans.append({'name':name,'from':row['from'],'to':row['to'],'tape1_mm':tapes[0],'tape2_mm':tapes[1],'note':row.get('note','')})
    if not spans:raise InputError(f'{path}: no spans')
    return spans


def xyz(values,where):
    if not isinstance(values,(list,tuple)) or len(values)!=3:raise InputError(f'{where}: expected [x, y, z]')
    return np.array([number(str(v),f'{where} coordinate') for v in values],float)


def split_line(line):
    for sep in (',',';','\t'):
        if sep in line:return [f.strip() for f in line.split(sep)],sep
    return line.split(),' '


def read_points(path):
    """{name: xyz (m)} from JSON, a name,x,y,z CSV or a CloudCompare point-list ASCII export (see the module docstring)."""
    path=Path(path)
    if not path.is_file():raise InputError(f'points file not found: {path}')
    text=path.read_text(encoding='utf-8-sig')
    if path.suffix.lower()=='.json':
        try:content=json.loads(text)
        except json.JSONDecodeError as error:raise InputError(f'{path}: invalid JSON ({error})') from None
        if not isinstance(content,dict) or not content:raise InputError(f'{path}: expected a non-empty object {{"name": [x, y, z]}}')
        return {str(k).strip():xyz(v,f'{path.name} point {k!r}') for k,v in content.items()}
    points={};labelled=None;rows=0
    for line_no,raw in enumerate(text.splitlines(),start=1):
        line=raw.strip()
        if not line or line.startswith(('#','//')):continue
        fields,sep=split_line(line);where=f'{path.name} line {line_no}'
        if len(fields)<3:raise InputError(f'{where}: expected label,x,y,z or x,y,z, got {line!r}')
        try:coords=np.array([float(f) for f in fields[-3:]],float)
        except ValueError:
            if not rows and not points and [f.lower() for f in fields[-3:]]==['x','y','z']:continue  # header row
            raise InputError(f'{where}: the last three fields must be x,y,z numbers, got {line!r}') from None
        if not np.isfinite(coords).all():raise InputError(f'{where}: non-finite coordinate in {line!r}')
        label=sep.join(fields[:-3]).strip() if len(fields)>3 else None
        if labelled is None:labelled=label is not None
        elif labelled!=(label is not None):raise InputError(f'{where}: labelled and unlabelled lines are mixed')
        name=label if labelled else str(rows)
        if not name:raise InputError(f'{where}: empty label')
        if name in points:raise InputError(f'{where}: point {name!r} appears twice')
        points[name]=coords;rows+=1
    if not points:raise InputError(f'{path}: no points')
    return points


def points_format(path):
    path=Path(path)
    if path.suffix.lower()=='.json':return 'json'
    lines=[l.strip() for l in path.read_text(encoding='utf-8-sig').splitlines() if l.strip() and not l.strip().startswith(('#','//'))]
    first=split_line(lines[0])[0]
    if [f.lower() for f in first[-3:]]==['x','y','z']:return 'csv_with_header'
    return 'label_xyz' if len(first)>3 else 'xyz_by_line_number'


def read_twin(path):
    """Provenance from reconstruction.json and bundle_manifest.json (read only)."""
    path=Path(path)
    if not path.is_dir():raise InputError(f'twin directory not found: {path}')
    def load(name):
        target=path/name
        if not target.is_file():return None
        try:return json.loads(target.read_text(encoding='utf-8'))
        except json.JSONDecodeError as error:raise InputError(f'{target}: invalid JSON ({error})') from None
    recon,manifest=load('reconstruction.json'),load('bundle_manifest.json')
    if recon is None and manifest is None:raise InputError(f'{path}: neither reconstruction.json nor bundle_manifest.json found')
    recon=recon or {};manifest=manifest or {};source=manifest.get('source_provenance') or recon.get('source_provenance') or {}
    floor=recon.get('floor_fit') or {};trajectory=(recon.get('commissioning') or {}).get('trajectory') or {}
    calibrations=source.get('calibrations')
    return {'path':str(path),'files_read':[n for n in ('reconstruction.json','bundle_manifest.json') if (path/n).is_file()],
        'map_revision':manifest.get('map_revision'),'hardware_validation':manifest.get('hardware_validation',source.get('hardware_validation')),
        'any_synthetic':source.get('any_synthetic'),'camera_calibrations':None if calibrations is None else len(calibrations),
        'camera_calibration_measured':source.get('all_camera_calibrations_measured'),
        'lidar_only':None if calibrations is None else not calibrations,'lidar_self_mask':None if not source else source.get('lidar_self_mask') is not None,
        'floor_fit_method':floor.get('method'),'floor_measured':floor.get('measured'),'declared_lidar_height_m':floor.get('declared_lidar_height_m'),
        'declared_minus_measured_lidar_height_m':floor.get('declared_minus_measured_m'),'display_mesh_source':recon.get('display_mesh_source'),
        'return_to_start_m':trajectory.get('start_to_end_m'),'path_m':trajectory.get('path_m'),
        'mesh_sha256':(manifest.get('authority_sha256') or {}).get('mesh.ply')}


def summarize(rows):
    rows=[r for r in rows if r['status']=='scored'];n=len(rows)
    out={'n':n,'spans':[r['name'] for r in rows]}
    keys=('bias_mm','sd_mm','mean_abs_mm','rmse_mm','max_abs_mm','p68_abs_mm','p95_abs_mm','bias_relative_pct','mean_abs_relative_pct','tape_abs_diff_mean_mm','tape_abs_diff_max_mm')
    if not n:return {**out,**dict.fromkeys(keys)}
    e=np.array([r['signed_error_mm'] for r in rows]);a=np.abs(e);rel=np.array([r['relative_error_pct'] for r in rows]);tape=np.abs([r['tape_diff_mm'] for r in rows])
    values=(e.mean(),e.std(ddof=1) if n>1 else None,a.mean(),math.sqrt(float(np.mean(e*e))),a.max(),np.percentile(a,68),np.percentile(a,95),
            rel.mean(),np.abs(rel).mean(),tape.mean(),tape.max())
    return {**out,**{k:None if v is None else float(v) for k,v in zip(keys,values)}}


def score(spans,points,tolerance_mm):
    rows=[];missing=[]
    for span in spans:
        t1,t2=span['tape1_mm'],span['tape2_mm'];mean=(t1+t2)/2;diff=t2-t1
        row={**span,'tape_mean_mm':mean,'tape_diff_mm':diff,'tape_flagged':abs(diff)>tolerance_mm,'over_3m':mean>LONG_MM,
             'twin_mm':None,'signed_error_mm':None,'abs_error_mm':None,'relative_error_pct':None}
        gone=[p for p in (span['from'],span['to']) if p not in points]
        if gone:
            missing+=[p for p in gone if p not in missing];row.update(status='missing_point',missing=gone)
        else:
            twin=float(np.linalg.norm(points[span['to']]-points[span['from']]))*1000;error=twin-mean
            row.update(status='scored',twin_mm=twin,signed_error_mm=error,abs_error_mm=abs(error),relative_error_pct=error/mean*100)
        rows.append(row)
    clean=[r for r in rows if not r['tape_flagged']];long=[r for r in rows if r['over_3m']]
    summary={'all':summarize(rows),'clean':summarize(clean),'over_3m':summarize(long),'over_3m_clean':summarize([r for r in long if not r['tape_flagged']])}
    warnings=[]
    if len(spans)<6:warnings.append(f'{len(spans)} spans; docs/LAB_DAY.md asks for 6-10')
    if summary['over_3m']['n']<2:warnings.append(f"{summary['over_3m']['n']} scored span(s) over 3 m; docs/LAB_DAY.md asks for at least two")
    for r in rows:
        if r['status']=='scored' and abs(r['relative_error_pct'])>GROSS_PCT:
            warnings.append(f"span {r['name']!r} is off by {r['relative_error_pct']:+.0f} %: check the picks and that points are in metres")
    return rows,sorted(missing),summary,warnings


def cell(value):return str(value).replace('|','\\|').replace('\n',' ')


def fmt(value,spec='.1f'):return '-' if value is None else format(value,spec)


def tape_text(value):return f'{value:g}'


def markdown(report):
    tol=report['inputs']['tape_tolerance_mm'];twin=report.get('twin')
    lines=['# Landmark spans: twin versus tape','',f"> {HONESTY}",'']
    if twin:
        calib={True:'measured',False:'NOT measured',None:'none (LiDAR-only twin)' if twin.get('lidar_only') else 'unknown'}[twin.get('camera_calibration_measured')]
        floor=twin.get('floor_fit_method') or 'unknown'
        if twin.get('declared_lidar_height_m') is not None:floor+=f", declared LiDAR height {twin['declared_lidar_height_m']} m"
        lines+=[f"Twin `{cell(twin['path'])}`: map revision `{twin.get('map_revision') or 'unknown'}`; camera calibration {calib}; floor fit {floor}"
                f"{'' if twin.get('return_to_start_m') is None else '; return-to-start error %.3f m' % twin['return_to_start_m']}"
                f"; hardware validation {twin.get('hardware_validation') or 'unknown'}.",'']
    lines+=[f"Spans `{cell(report['inputs']['spans'])}`, points `{cell(report['inputs']['points'])}` ({report['inputs']['points_format']}); "
            f"tape pairs more than {tape_text(tol)} mm apart are flagged.",'',
            '| Span | From | To | Tape 1 (mm) | Tape 2 (mm) | Tape mean (mm) | Tape diff (mm) | Twin (mm) | Error (mm) | \\|Error\\| (mm) | Rel. error (%) | Note |',
            '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in report['spans']:
        note=[r['note']] if r['note'] else []
        if r['tape_flagged']:note.append(f"tape pair differs by {abs(r['tape_diff_mm']):g} mm (> {tape_text(tol)} mm): excluded from the clean summary")
        if r['status']=='missing_point':note.append('not scored: missing point '+', '.join(r['missing']))
        lines.append('| '+' | '.join(cell(v) for v in (r['name'],r['from'],r['to'],tape_text(r['tape1_mm']),tape_text(r['tape2_mm']),fmt(r['tape_mean_mm']),
            f"{r['tape_diff_mm']:+g}" if r['tape_diff_mm'] else '0',fmt(r['twin_mm']),fmt(r['signed_error_mm'],'+.1f'),fmt(r['abs_error_mm']),fmt(r['relative_error_pct'],'+.2f'),'; '.join(note)))+' |')
    labels={'all':'All spans','clean':f'Tape pairs within {tape_text(tol)} mm','over_3m':'Over 3 m','over_3m_clean':f'Over 3 m, tape pairs within {tape_text(tol)} mm'}
    lines+=['','| Summary | n | Bias (mm) | Mean \\|error\\| (mm) | RMSE (mm) | Max \\|error\\| (mm) | P68 \\|error\\| (mm) | P95 \\|error\\| (mm) | Mean \\|rel. error\\| (%) | Tape diff mean / max (mm) |',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for key,label in labels.items():
        s=report['summary'][key]
        lines.append(f"| {label} | {s['n']} | {fmt(s['bias_mm'],'+.1f')} | {fmt(s['mean_abs_mm'])} | {fmt(s['rmse_mm'])} | {fmt(s['max_abs_mm'])} | "
                     f"{fmt(s['p68_abs_mm'])} | {fmt(s['p95_abs_mm'])} | {fmt(s['mean_abs_relative_pct'],'.2f')} | {fmt(s['tape_abs_diff_mean_mm'])} / {fmt(s['tape_abs_diff_max_mm'])} |")
    lines+=['','Percentiles of |error| use linear interpolation (numpy.percentile); with few spans they are close to the largest errors.']
    lines+=['']+[f'- {n}' for n in NOTES]
    if report['missing_points']:lines+=['',f"**Missing points (spans not scored):** {', '.join(cell(p) for p in report['missing_points'])}"]
    if report['warnings']:lines+=['','**Warnings:**']+[f'- {cell(w)}' for w in report['warnings']]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--spans',required=True,help='CSV: name,from,to,tape1_mm,tape2_mm[,note]')
    p.add_argument('--points',required=True,help='JSON {name:[x,y,z]}, CSV name,x,y,z or a CloudCompare point-list export (metres)')
    p.add_argument('--twin',help='twin directory, read only, for provenance');p.add_argument('--tape-tolerance-mm',type=float,default=5.)
    p.add_argument('--output',help='JSON report');p.add_argument('--markdown',help='markdown report')
    args=p.parse_args(argv)
    try:
        if not math.isfinite(args.tape_tolerance_mm) or args.tape_tolerance_mm<0:raise InputError('--tape-tolerance-mm must be >= 0')
        spans=read_spans(args.spans);points=read_points(args.points);twin=read_twin(args.twin) if args.twin else None
    except InputError as error:
        print(f'error: {error}',file=sys.stderr);return 2
    rows,missing,summary,warnings=score(spans,points,args.tape_tolerance_mm)
    referenced={p for s in spans for p in (s['from'],s['to'])}
    report={'tool':'tools/score_landmarks.py','complete':not missing,'honesty':HONESTY,'notes':NOTES,
        'inputs':{'spans':str(args.spans),'spans_sha256':sha256(args.spans),'points':str(args.points),'points_sha256':sha256(args.points),
                  'points_format':points_format(args.points),'points_unit':'m','tape_tolerance_mm':args.tape_tolerance_mm,'over_3m_threshold_mm':LONG_MM},
        'twin':twin,'spans':rows,'missing_points':missing,'unused_points':sorted(set(points)-referenced),
        'flagged_spans':[r['name'] for r in rows if r['tape_flagged']],'summary':summary,'warnings':warnings}
    if args.output:Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    if args.markdown:Path(args.markdown).parent.mkdir(parents=True,exist_ok=True);Path(args.markdown).write_text(markdown(report),encoding='utf-8',newline='\n')
    s=summary['all'];c=summary['clean']
    print(f"Scored {s['n']}/{len(rows)} spans: bias {fmt(s['bias_mm'],'+.1f')} mm, RMSE {fmt(s['rmse_mm'])} mm, max {fmt(s['max_abs_mm'])} mm; "
          f"without {len(report['flagged_spans'])} flagged tape pair(s): n {c['n']}, RMSE {fmt(c['rmse_mm'])} mm. {HONESTY}")
    for w in warnings:print(f'warning: {w}')
    if missing:print(f"error: missing points (spans not scored): {', '.join(missing)}",file=sys.stderr);return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
