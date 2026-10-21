-- >>> kokoro-say >>>
-- Read text aloud with Kokoro-82M.
-- Press again while it is speaking to stop.
o.bind("SUPER + ALT + R", "Read selection aloud", "kokoro-say toggle primary")
o.bind("SUPER + SHIFT + ALT + R", "Read clipboard aloud", "kokoro-say toggle clipboard")
-- <<< kokoro-say <<<
