# omc-managed fish title integration v2
# Pins this iTerm2 tab's title to the current Git branch (README: "Tab titles").
# Provisioned by `omc shell-integration fish`, removed by `omc uninstall`. The first
# line is omc's ownership marker: keep it, or omc refuses to manage this file.
# fish 3.7 syntax (Ubuntu CI); the prompt never waits on Python.

# Keep this activation predicate in sync with src/omc/shells/fish.py:_ITERM2_HOOK_PREDICATE.
status is-interactive; or return
string match -rq -- '^(w[0-9]+t[0-9]+p[0-9]+:)?[0-9A-Fa-f-]{36}$' "$ITERM_SESSION_ID"; or return
if test "$TERM_PROGRAM" != iTerm.app; and test "$LC_TERMINAL" != iTerm2
    return
end
# Every shell inside a multiplexer inherits ONE tab's session id.
set -q TMUX; and return
set -q STY; and return

# Re-sourcing (conf.d, then an `omc start` -C command) must leave one handler per
# event and nothing from an earlier copy: erase by enumeration, not by a fixed list.
for __omc_name in (functions -a | string match -r '^__omc_title_.*')
    functions -e $__omc_name
end
set -e __omc_name
# State is reset; the configuration variable __omc_title_helper is preserved.
set -e __omc_title_last_request
set -e __omc_title_keys
set -e __omc_title_branches
set -e __omc_title_hinted
set -e __omc_title_marker_seen
set -e __omc_title_started
set -e __omc_title_moved
set -g __omc_title_uuid (string replace -r '^w[0-9]+t[0-9]+p[0-9]+:' '' -- $ITERM_SESSION_ID)
if set -q OMC_HOME; and test -n "$OMC_HOME"
    set -g __omc_title_home $OMC_HOME
else
    set -g __omc_title_home $HOME/.omc
end

function __omc_title_remember --argument-names name
    set -l key (command git rev-parse --absolute-git-dir 2>/dev/null)
    test -n "$key"; or return 0
    set -l index (contains -i -- $key $__omc_title_keys)
    if test -n "$index"
        set -g __omc_title_branches[$index] $name
    else
        set -ga __omc_title_keys $key
        set -ga __omc_title_branches $name
    end
end

function __omc_title_dispatch --argument-names outcome
    set -l helper $__omc_title_helper
    if test (count $helper) -eq 0
        # Resolved at refresh time so a PATH fixed later in the session is picked up.
        set helper (command -s omc)
        if test -z "$helper"
            if not set -q __omc_title_hinted
                set -g __omc_title_hinted 1
                echo 'omc: fish title hook found no `omc` on PATH; `omc shell-integration fish disable` removes the hook' >&2
            end
            set -e __omc_title_last_request
            return 0
        end
    end
    set -l dir $__omc_title_home/title-request
    test -d $dir; or command /bin/mkdir -p $dir 2>/dev/null; or return 0
    set -l request $dir/$__omc_title_uuid-$fish_pid
    # Builtin echo + redirection only; the helper serializes and re-reads (omc title apply).
    echo $outcome >$request; or return 0
    $helper title apply $request >/dev/null 2>&1 &
    disown $last_pid 2>/dev/null
end

function __omc_title_refresh
    set -g __omc_title_started 1
    test "$OMC_FISH_TITLE_DISABLE" = 1; and return 0
    set -l marker $__omc_title_home/title-failed/$__omc_title_uuid
    if test -e $marker
        set -l mtime (path mtime -- $marker)
        set -q __omc_title_marker_seen; or set -g __omc_title_marker_seen 0
        if test -n "$mtime"; and test $mtime -gt $__omc_title_marker_seen
            # One hint and one retry per failure, not one per prompt during the cooldown.
            set -g __omc_title_marker_seen $mtime
            read -l line <$marker
            test -n "$line"; and echo $line >&2
            set -e __omc_title_last_request
        end
    end
    # git absent from PATH changes nothing (spec §2). Without this guard fish itself prints
    # "fish: Unknown command: git" to stderr; a 2>/dev/null on the command cannot silence it.
    command -s git >/dev/null 2>&1; or return 0
    set -l ref (command git symbolic-ref --quiet HEAD 2>/dev/null)
    set -l code $status
    set -l desired
    switch $code
        case 0
            # Full symbolic ref, refs/heads/ stripped literally: a tag never collides.
            set -l name (string replace -r '^refs/heads/' '' -- $ref)
            set desired "set $name"
            # A directory change may land in another worktree on the SAME branch name: record
            # its key too, or a later detach there releases instead of pinning.
            if test "$desired" != "$__omc_title_last_request"; or set -q __omc_title_moved
                __omc_title_remember $name
            end
            set -e __omc_title_moved
        case 1
            set -l key (command git rev-parse --absolute-git-dir 2>/dev/null)
            set -l index
            test -n "$key"; and set index (contains -i -- $key $__omc_title_keys)
            if test -n "$index"
                set desired "set $__omc_title_branches[$index]"
            else
                set desired release
            end
        case 128
            set desired release
        case '*'
            return 0
    end
    test "$desired" = "$__omc_title_last_request"; and return 0
    set -g __omc_title_last_request $desired
    __omc_title_dispatch $desired
end

# The first prompt performs the first refresh; PWD events before it are ignored so
# config.fish PATH changes take effect first. `omc start` calls __omc_title_refresh
# itself because a -C command emits neither fish_preexec nor fish_prompt.
function __omc_title_prompt --on-event fish_prompt
    __omc_title_refresh
end

function __omc_title_preexec --on-event fish_preexec
    set -q __omc_title_started; and __omc_title_refresh
end

function __omc_title_pwd --on-variable PWD
    set -g __omc_title_moved 1
    set -q __omc_title_started; and __omc_title_refresh
end
