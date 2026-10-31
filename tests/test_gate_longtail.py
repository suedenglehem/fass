"""Long-tail gate widening — the vocabulary the LLM second try reaches for.

Every positive case mirrors a command the two-LLM qualification campaign
generated that ffmpeg 9.0.1 itself accepted (rc=0, or a post-parse rc
that proves the flags are real).  Every negative case is something
ffmpeg itself rejects — rc=8 hallucinated option, invalid stream
label, non-existent enum value — or a shell construct that is not a
bare ffmpeg invocation.  Doctrine: rc=8 at the option-parse phase
means the flag is a hallucination and must stay rejected; any other
rc means the flag exists and the gate must accept it.
"""
from fass import execute

V = execute.validate_argv


def _run(*extra, out="out.mp4"):
    return V(["ffmpeg", "-hide_banner", "-i", "a.mp4", *extra, out])


# ------------------------------------------------- global expert flags

def test_frame_rate_forms():
    assert _run("-r", "25") == []
    assert _run("-r", "24000/1001") == []
    assert _run("-framerate", "30") == []
    assert _run("-r", "abc")


def test_loop_and_timestamp_offset():
    assert V(["ffmpeg", "-stream_loop", "-1", "-i", "live.ts",
              "-c:a", "copy", "o.mp4"]) == []
    assert V(["ffmpeg", "-stream_loop", "3", "-i", "a.mp4", "o.mp4"]) == []
    assert V(["ffmpeg", "-loop", "-1", "-i", "bg.png", "-t", "30",
              "-f", "null", "-"]) == []
    assert V(["ffmpeg", "-itsoffset", "-2.5", "-i", "b.mp4",
              "-c", "copy", "o.mp4"]) == []
    assert V(["ffmpeg", "-itsoffset", "1:30:00", "-i", "b.mp4",
              "-c", "copy", "o.mp4"]) == []


def test_fflags_joined():
    assert _run("-fflags", "+genpts") == []
    assert _run("-fflags", "+genpts+ignorets") == []
    assert _run("-fflags", "+genpts-bitless")  # not in the flag set
    assert _run("-fflags", "+not_a_flag")


def test_nostats_boolean():
    assert V(["ffmpeg", "-nostats", "-i", "a.mp4", "-f", "null", "-"]) == []


def test_two_pass():
    assert _run("-c:v", "libx264", "-b:v", "1145k", "-pass", "1",
                out="out.ts") == []
    assert _run("-pass", "3")


def test_muxer_timing():
    # muxrate / muxpreload / max_delay — the mpegts jitter controls
    assert _run("-f", "mpegts", "-muxrate", "10000k", out="o.ts") == []
    assert _run("-f", "mpegts", "-muxrate", "20M", out="o.ts") == []
    assert _run("-f", "mpegts", "-muxpreload", "0.44", out="o.ts") == []
    assert _run("-f", "mpegts", "-muxrate", "fast", out="o.ts")
    assert _run("-max_delay", "0") == []
    assert _run("-max_delay", "2000000") == []
    assert _run("-max_delay", "-5")
    assert _run("-max_delay", "soon")


def test_gop_and_concat_safe():
    assert _run("-g", "15") == []
    assert _run("-g", "0") == []
    assert V(["ffmpeg", "-f", "concat", "-safe", "0", "-i", "list.txt",
              "-c", "copy", "o.mp4"]) == []
    assert _run("-safe", "2")


def test_timecode_and_sample_fmt():
    assert _run("-timecode", "10:00:00:00") == []
    assert _run("-timecode", "10:20:30") == []
    assert _run("-timecode", "10:00")
    assert _run("-sample_fmt", "s16") == []
    assert _run("-sample_fmt", "flt") == []  # 32-bit float is real
    assert _run("-sample_fmt", "fltp") == []
    assert _run("-sample_fmt", "dblp") == []
    assert _run("-sample_fmt", "s64p") == []
    assert _run("-sample_fmt", "u8p") == []
    assert _run("-sample_fmt", "flt8")


def test_channel_layout_and_map_channel():
    assert _run("-channel_layout", "5.1") == []
    assert _run("-channel_layout", "stereo") == []
    assert _run("-channel_layout", "hex")
    assert _run("-map_channel", "0.0.0:0.0.0") == []
    assert _run("-map_channel", "x.0.0:0.0.0")


def test_ac_forms():
    assert _run("-ac", "2") == []
    assert _run("-ac", "4") == []
    assert _run("-ac", "6") == []
    assert _run("-ac", "0")


# ------------------------------------------------- color tags

def test_color_tag_branches():
    assert _run("-color_primaries", "bt709") == []
    assert _run("-color_trc", "bt709") == []
    assert _run("-colorspace", "bt709") == []
    assert _run("-color_range", "tv") == []
    assert _run("-color_range", "pc") == []
    assert _run("-color_range", "limited") == []
    assert _run("-color_range", "full") == []
    assert _run("-color_range", "0") == []
    assert _run("-color_range", "resv")
    assert _run("-color_primaries", "srgb")  # not a tag in the set


# ------------------------------------------------- sample-rate specs

def test_ar_forms():
    assert _run("-ar", "48000") == []
    assert _run("-ar:a", "48000") == []
    assert _run("-ar:a:0", "48000") == []
    assert _run("-ar:a", "48")  # not a Hz value


# ------------------------------------------------- per-stream specifiers

def test_stream_indexed_specs():
    assert V(["ffmpeg", "-i", "a.mp4", "-b:v:0", "15M",
              "-b:v:1", "8M", "-s:v:1", "640x360", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-profile:v:0", "high",
              "-profile:v:1", "main", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-c:s:1", "mov_text", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-minrate:v:0", "2M",
              "-maxrate:v:0", "10M", "-bufsize:v:0", "20M", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-c:v:0", "libx264", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-c:v:0", "not_a_codec",
              "o.mp4"])  # still codec-checked through the spec


def test_disposition_specs():
    assert V(["ffmpeg", "-i", "a.mp4", "-disposition:s:0", "default",
              "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-disposition:a:1", "comments",
              "o.mkv"]) == []
    # ffmpeg 9.0.1 accepts the singular form too (campaign case 37)
    assert V(["ffmpeg", "-i", "a.mp4", "-disposition:a:1", "comment",
              "o.mkv"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-disposition:a:1", "bogus",
              "o.mkv"])
    assert V(["ffmpeg", "-i", "a.mp4", "-disposition:a:1", "default=2",
              "o.mkv"])


def test_metadata_specs():
    assert V(["ffmpeg", "-i", "a.mp4", "-metadata:s:a:0", "language=eng",
              "o.mkv"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-metadata", "title=Clip",
              "o.mp4"]) == []


def test_positional_map_specs():
    # whole-input / bare-index / type-only forms (campaign cases 59/96)
    assert V(["ffmpeg", "-i", "a.mp4", "-map", "0", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-i", "b.mp4", "-map", "0:0",
              "-map", "1:s", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-map", "0:v:0", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-map", "0:xyz", "o.mp4"])
    assert V(["ffmpeg", "-i", "a.mp4", "-map", "[v]", "o.mp4"]) == []


# ------------------------------------------------- mpegts program table

def test_mpegts_pid_options():
    base = ["ffmpeg", "-i", "a.mp4"]
    assert V(base + ["-f", "mpegts", "-mpegts_pmt_start_pid", "0x0300",
                     "-mpegts_start_pid", "0x100", "o.ts"]) == []
    assert V(base + ["-f", "mpegts", "-streamid", "0:0x0300",
                     "-streamid", "1:0x0301", "o.ts"]) == []
    assert V(base + ["-f", "mpegts", "-program", "title=Main:st=0,st=1",
                     "o.ts"]) == []
    assert V(base + ["-f", "mpegts", "-pat_period", "0.1",
                     "-sdt_period", "0.5", "o.ts"]) == []
    assert V(base + ["-f", "mpegts", "-nit_period", "1",
                     "-pcr_period", "0.02", "o.ts"]) == []
    # bool kind = a 0/1 value flag on the CLI (verified on 9.0.1)
    assert V(base + ["-f", "mpegts", "-pes_payload_size", "320",
                     "-mpegts_copyts", "1", "-omit_video_pes_length", "1",
                     "o.ts"]) == []
    assert V(base + ["-f", "mpegts", "-tables_version", "7", "o.ts"]) == []


def test_mpegts_pid_options_reject_bad_values():
    # kind/range validation now applies (they are registry format options)
    assert V(["ffmpeg", "-i", "a.mp4", "-f", "mpegts",
              "-pat_period", "abc", "o.ts"])
    assert V(["ffmpeg", "-i", "a.mp4", "-f", "mpegts",
              "-tables_version", "99", "o.ts"])  # range is [0, 31]
    assert V(["ffmpeg", "-i", "a.mp4", "-f", "mpegts",
              "-pes_payload_size", "999999", "o.ts"])  # range is [0, 65535]
    # the mpegts group is mpegts-only: pat_period is not a mov option
    assert V(["ffmpeg", "-i", "a.mp4", "-pat_period", "0.1", "o.mp4"])


# ------------------------------------------------- profiles and bitrates

def test_profile_v_forms():
    assert _run("-profile:v", "4444") == []
    assert _run("-profile:v", "2") == []
    assert _run("-profile:v", "proxy") == []
    assert _run("-profile:v", "high") == []
    assert _run("-profile:v", "not_a_profile")


def test_bitrate_forms():
    assert _run("-b:v", "15M") == []
    assert _run("-b:v", "1145k") == []
    assert _run("-b:a", "384k") == []
    assert _run("-minrate", "2M", "-maxrate", "10M", "-bufsize", "20M") == []
    assert _run("-b:v", "15X")


# ------------------------------------------------- codecs and formats

def test_longtail_codecs():
    assert _run("-c:v", "prores", "-profile:v", "proxy", out="out.mov") == []
    assert V(["ffmpeg", "-i", "a.mp4", "-c:v", "ffv1", "o.mkv"]) == []
    assert _run("-c:v", "mpeg2video", "-b:v", "15M", out="out.ts") == []
    assert _run("-c:a", "mp2", "-b:a", "384k", out="out.ts") == []
    assert V(["ffmpeg", "-i", "a.mp4", "-c:s", "srt", "o.mkv"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-c:s", "mov_text", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-c:v", "not_a_codec",
              "o.mp4"])


def test_longtail_formats_and_pix_fmts():
    assert V(["ffmpeg", "-i", "a.mp4", "-f", "rawvideo", "o.yuv"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-f", "mpegts", "o.ts"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-f", "not_a_format",
              "o.bin"])
    assert _run("-pix_fmt", "yuv422p") == []
    assert _run("-pix_fmt", "yuv444p") == []
    assert _run("-pix_fmt", "yuva444p10le") == []
    assert _run("-pix_fmt", "not_a_fmt")


# ------------------------------------------------- long-tail filters

def test_longtail_filters_with_args():
    graphs = [
        # the 11-entry gate widening (M11.4) — realistic campaign args
        "[0:v]pan=stereo|c0=c0+c1[a]",
        "[0:a]afftdn=nf=-25[a]",
        "[0:a]asetpts=PTS-STARTPTS[a]",
        # expressions with commas ride inside single quotes (campaign 75)
        "[0:v]select='eq(n,25)'[v]",
        "[0:v][1:v]blend=all_mode=difference[v]",
        "[0:v][1:v]alphamerge[v]",
        "[0:v][1:v]xstack=inputs=2:layout=0_0|h0_0[v]",
        "[0:a]asplit=2[a1][a2]",
        "[0:a][1:a]sidechaincompress=threshold=0.02[a]",
        "[0:v]geq=lum='p(W/2,H/2)'[v]",
        # the 7-entry evidence widening — name-only, args unchecked
        "[0:v]zscale=m=bt2020n[base]",
        "[0:v]lutrgb=r='clip(val*4,0,255)'[v]",
        "[0:v]colorchannelmixer=rr=0.3:rg=0.59:rb=0.11[v]",
        "[0:v]tpad=start_duration=1[v]",
        "[0:v][1:v]scale2ref=640:360[v][r]",
        "[0:v]despill=mode=green:influence=0.5[v]",
        "[0:a]channelmap=map=0-R:1-L[a]",
    ]
    for frag in graphs:
        argv = (["ffmpeg", "-i", "a.mp4", "-i", "b.mp4",
                 "-filter_complex", frag + "[out]", "-map", "[out]",
                 "o.mp4"])
        assert V(argv) == [], argv


def test_yadif_numeric_aliases():
    assert _run("-vf", "yadif=mode=send_frame:parity=1:deint=0") == []
    assert _run("-vf", "yadif=parity=tff") == []
    assert _run("-vf", "yadif=mode=bogus")


def test_asetrate_expression():
    # string kind — rate math expressions are the point (campaign 56)
    assert V(["ffmpeg", "-i", "a.mp4", "-af",
              "asetrate=48000*1.122462,aresample=48000", "o.mp4"]) == []


def test_mpeg2video_private_option():
    # -sc_threshold is an mpeg2video encoder option (campaign 19)
    assert V(["ffmpeg", "-i", "a.mp4", "-c:v", "mpeg2video",
              "-b:v", "15M", "-sc_threshold", "0", "o.ts"]) == []


def test_stream_labels_and_setpts():
    assert V(["ffmpeg", "-i", "a.mp4", "-filter_complex",
              "[0:v:0]scale=640:360[v]", "-map", "[v]", "o.mp4"]) == []
    assert V(["ffmpeg", "-i", "a.mp4", "-af",
              "asetpts=PTS-STARTPTS,volume=0.5", "o.mp4"]) == []
    # the pid= stream specifier is NOT real ffmpeg (campaign 11, rc=234)
    assert V(["ffmpeg", "-i", "a.mp4", "-filter_complex",
              "[0:v:pid=0x0101]scale=640:360[v]", "-map", "[v]", "o.mp4"])


# ------------------------------------------------- negatives stay rejected

def test_hallucinated_pid_flags_still_rejected():
    # rc=8 hallucinations from the campaign — must stay unknown
    for flag in ("-pid", "-service_id", "-video_pid", "-audio_pid",
                 "-mpegts_video_pid", "-mpegts_audio_pid",
                 "-mpegts_pmt_pid"):
        p = V(["ffmpeg", "-i", "a.mp4", flag, "0x0100", "o.ts"])
        assert any("unknown flag" in x for x in p), (flag, p)


def test_shell_chains_rejected():
    # '&&' concatenation and pipes are not bare ffmpeg invocations
    p = V(["ffmpeg", "-y", "-i", "a.mp4", "-f", "null", "/dev/null",
           "&&", "ffmpeg", "-y", "-i", "a.mp4", "o.mp4"])
    assert any("bare token" in x for x in p)
    p = V(["ffmpeg", "-i", "a.wav", "-af", "loudnorm=I=-23", "-f", "null",
           "-", "2>&1", "|", "tee", "measure.log"])
    assert p


def test_bogus_enums_still_rejected():
    assert V(["ffmpeg", "-i", "a.mp4", "-color_range", "resv", "o.mp4"])
    assert V(["ffmpeg", "-i", "a.mp4", "-sample_fmt", "flt8", "o.mp4"])
    assert V(["ffmpeg", "-i", "a.mp4", "-pass", "3", "o.mp4"])
    # tonemap=pl is not a real method (campaign 72)
    assert V(["ffmpeg", "-i", "a.mp4", "-filter_complex",
              "[0:v]tonemap=pl[v]", "-map", "[v]", "o.mp4"])
    assert V(["ffmpeg", "-i", "a.mp4", "-disposition:a:1", "bogus",
              "o.mp4"])
    assert V(["ffmpeg", "-i", "a.mp4", "-map", "0:xyz", "o.mp4"])
