"""Real codec regression: two storyboard cuts must retain sound for 15 seconds."""
import array
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import nle


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
class StoryboardAudioRenderTests(unittest.TestCase):
    def test_fifteen_second_audio_survives_scene_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'media').mkdir()
            src=root/'media/tone.wav'
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=15:sample_rate=48000',str(src)],check=True)
            project={'name':'Audio boundary','canvas':{'width':64,'height':64},
                     'media':[{'id':'tone','src':'media/tone.wav','kind':'audio','hasAudio':True,'duration':15}],
                     'tracks':[{'id':'A1','kind':'audio','clips':[
                         {'id':'first','mediaId':'tone','start':0,'in':0,'out':7.5},
                         {'id':'second','mediaId':'tone','start':7.5,'in':7.5,'out':15}]}]}
            output=root/nle.export_project(project,root).lstrip('/')
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-f','f32le','-ac','1','-ar','48000','-'])
            pcm=array.array('f');pcm.frombytes(raw)
            self.assertAlmostEqual(len(pcm)/48000,15,delta=.08)
            # Inspect small windows on both sides of the cut and at the tail.
            for at in [7.4,7.48,7.5,7.52,7.6,14.8]:
                segment=pcm[int(at*48000):int((at+.02)*48000)]
                rms=math.sqrt(sum(x*x for x in segment)/len(segment))
                self.assertGreater(rms,.03,'Audio dropout near '+str(at))
                self.assertTrue(all(math.isfinite(x) and abs(x)<=1 for x in segment))

    def test_magia_does_not_force_silent_mode(self):
        script=(Path(__file__).parents[1]/'app.js').read_text()
        handler=script[script.index('async function createMagiaStoryboard'):script.index('function storyboardIdentityNotes')]
        self.assertNotIn("audio_mode:'silent'",handler)
        self.assertIn("audio_mode:(draft.output||{}).audio_mode||'effects'",handler)
