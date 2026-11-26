"""Run a queued facial geometry preview or save its chosen intensity."""
from pathlib import Path
import sys
import time
import traceback

from face_refine_api import COPY_KIND, PREVIEW_KIND, file_hash
from studio_workspace import read, write


def run(folder):
    from face_refine_engine import reconstruct_head, detector_path
    from face_refine_geometry import apply_patch, build_patch
    start = time.monotonic()
    recipe = read(folder / 'face-request.json')
    settings = read(folder / 'settings.json')
    kind = settings['source_kind']
    source = Path(recipe['source'])
    def status(state, stage, progress, **extra):
        write(folder / 'status.json', dict(state=state, stage=stage, progress=progress, operation=kind, **extra))
        print(stage, flush=True)

    def validate_source():
        if source.resolve() != source or not source.is_file() or file_hash(source) != recipe['sha256']:
            raise ValueError('The source model changed or was removed. Run the preview again.')

    patch = folder / 'patch.bin'
    obj = folder / 'head.obj'
    temporary = folder / 'face-copy.tmp.glb'
    try:
        validate_source()
        if kind == PREVIEW_KIND:
            status('running', 'Reconstructing the head from the portrait', 10)
            reconstruct_head(folder / 'portrait.png', obj)
            status('running', 'Aligning eyes, nose and lips', 72)
            result = build_patch(source, obj, patch, detector_path=detector_path())
            validate_source()
            status('done', 'Face preview ready', 100, **result)
        elif kind == COPY_KIND:
            status('running', 'Applying facial relief at the chosen intensity', 20)
            strength = settings['face_refinement']['strength']
            apply_patch(source, patch, temporary, strength)
            validate_source()
            result = dict(recipe['result'], name=settings['name'], face_refinement=settings['face_refinement'],
                          face_refinement_seconds=round(time.monotonic()-start, 2))
            temporary.replace(folder / 'model.glb')
            write(folder / 'result.json', result)
            status('done', 'Refined face copy saved', 100, result=result)
        else:
            raise ValueError('Unknown face preparation operation.')
    except Exception as error:
        traceback.print_exc()
        patch.unlink(missing_ok=True)
        status('error', 'Face refinement stopped', 0, error=str(error))
        raise
    finally:
        obj.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        if kind == COPY_KIND:
            patch.unlink(missing_ok=True)
            (folder / 'face-request.json').unlink(missing_ok=True)


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
