"""Package a photo-capture model of the lab for the studio's visual-only layer.

Usage: python tools/package_lab_appearance.py MODEL.glb OUTPUT_DIR [--transform 16 numbers | --transform-json FILE]
       [--method none|manual|measured_points|lidar_registration] [--rmse-m R] [--checked-points N]
       [--device TEXT] [--tool TEXT] [--captured YYYY-MM-DD] [--notes TEXT]

The transform maps capture coordinates to the twin's world_z_up frame (row-major 4x4,
rotation with one uniform scale). Serve the result with
`python -m command_center --appearance OUTPUT_DIR`. See docs/LAB_APPEARANCE.md.
"""
import argparse,json,sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from command_center.appearance import ALIGNMENT_METHODS,package_appearance


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('model',type=Path);parser.add_argument('output',type=Path)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--transform',type=float,nargs=16);group.add_argument('--transform-json',type=Path)
    parser.add_argument('--method',choices=ALIGNMENT_METHODS,default='none')
    parser.add_argument('--rmse-m',type=float);parser.add_argument('--checked-points',type=int)
    for name in ('device','tool','captured','notes'):parser.add_argument(f'--{name}')
    args=parser.parse_args(argv)
    transform=args.transform if args.transform else json.loads(args.transform_json.read_text()) if args.transform_json else None
    provenance={name:getattr(args,name) for name in ('device','tool','captured','notes') if getattr(args,name)}
    try:manifest=package_appearance(args.model,args.output,transform=transform,method=args.method,rmse_m=args.rmse_m,
                                    checked_points=args.checked_points,provenance=provenance)
    except (ValueError,FileExistsError) as error:parser.error(str(error))
    print(json.dumps({'output':str(args.output),'file':manifest['file'],'sha256':manifest['sha256'],'alignment':manifest['alignment']}))


if __name__=='__main__':main()
