"""Common UK forename variants mapped to formal forms.

Each nickname maps to one or more formal names. A record's set of "first name
forms" is its own name plus any formal names it maps to, so "Bill" and
"William" share the form "william", and "Bill" and "Will" share it too.

Keep entries conservative: every mapping widens what can match.
"""

from __future__ import annotations

_RAW: dict[str, tuple[str, ...]] = {
    # A
    "abby": ("abigail",), "abbie": ("abigail",), "abi": ("abigail",),
    "al": ("alan", "albert", "alexander", "alfred"), "alf": ("alfred",), "alfie": ("alfred",),
    "alex": ("alexander", "alexandra"), "alexa": ("alexandra",), "sandy": ("alexander", "alexandra"),
    "alec": ("alexander",), "ali": ("alison", "alistair"), "allie": ("alison",),
    "andy": ("andrew",), "drew": ("andrew",), "annie": ("ann", "anne"), "anna": ("ann", "anne"),
    "archie": ("archibald",), "art": ("arthur",), "artie": ("arthur",),
    # B
    "barbie": ("barbara",), "babs": ("barbara",), "barney": ("bernard",), "bart": ("bartholomew",),
    "becky": ("rebecca",), "becca": ("rebecca",), "bec": ("rebecca",), "ben": ("benjamin",),
    "benny": ("benjamin",), "bert": ("albert", "herbert", "robert"), "bertie": ("albert", "herbert"),
    "beth": ("elizabeth",), "betty": ("elizabeth",), "bess": ("elizabeth",), "bessie": ("elizabeth",),
    "bill": ("william",), "billy": ("william",), "bob": ("robert",), "bobby": ("robert",),
    "brad": ("bradley",),
    # C
    "cathy": ("catherine", "kathryn"), "cath": ("catherine",), "cass": ("cassandra",),
    "charlie": ("charles", "charlotte"), "chas": ("charles",), "chaz": ("charles",),
    "chris": ("christopher", "christine", "christina"), "kit": ("christopher",),
    "chrissy": ("christine",), "christy": ("christine", "christopher"), "chuck": ("charles",),
    "clem": ("clement",), "cliff": ("clifford",), "connie": ("constance",),
    # D
    "dan": ("daniel",), "danny": ("daniel",), "dani": ("danielle",), "dave": ("david",),
    "davy": ("david",), "davie": ("david",), "deb": ("deborah",), "debbie": ("deborah",),
    "debs": ("deborah",), "del": ("derek",), "des": ("desmond",), "di": ("diana", "diane"),
    "dick": ("richard",), "dom": ("dominic",), "don": ("donald",), "donny": ("donald",),
    "dot": ("dorothy",), "dotty": ("dorothy",), "doug": ("douglas",),
    # E
    "ed": ("edward", "edmund"), "eddie": ("edward",), "eddy": ("edward",), "ted": ("edward",),
    "teddy": ("edward",), "ellie": ("eleanor", "elizabeth"), "elle": ("eleanor",),
    "em": ("emily", "emma"), "emmy": ("emily",), "eric": ("frederick",),
    "ernie": ("ernest",),
    # F
    "fay": ("faith",), "fi": ("fiona",), "flo": ("florence",), "flossie": ("florence",),
    "fran": ("frances",), "fanny": ("frances",), "frank": ("francis",), "frankie": ("francis", "frances"),
    "fred": ("frederick",), "freddie": ("frederick",), "freddy": ("frederick",),
    # G
    "gabby": ("gabrielle",), "gaz": ("gareth", "gary"), "gen": ("genevieve",),
    "geoff": ("geoffrey",), "jeff": ("jeffrey", "geoffrey"), "gerry": ("gerald", "gerard"),
    "gill": ("gillian",), "gillie": ("gillian",), "ginny": ("virginia",),
    "greg": ("gregory",), "gus": ("angus", "augustus"),
    # H
    "hal": ("henry", "harold"), "hank": ("henry",), "harry": ("henry", "harold"),
    "hattie": ("harriet",), "hatty": ("harriet",), "herb": ("herbert",), "howie": ("howard",),
    # I
    "issy": ("isabel", "isabella"), "izzy": ("isabel", "isabella"), "bella": ("isabella",),
    # J
    "jack": ("john",), "johnny": ("john",), "jon": ("jonathan", "john"), "jonny": ("jonathan",),
    "jake": ("jacob",), "jamie": ("james",), "jim": ("james",), "jimmy": ("james",),
    "jan": ("janet", "janice"), "jen": ("jennifer",), "jenny": ("jennifer",), "jess": ("jessica",),
    "jessie": ("jessica", "janet"), "jo": ("joanne", "josephine", "joanna"), "jody": ("joanne",),
    "joe": ("joseph",), "joey": ("joseph",), "josh": ("joshua",), "judy": ("judith",),
    "jules": ("julian", "julia", "juliet"), "julie": ("julia", "juliet"),
    # K
    "kate": ("katherine", "catherine"), "katie": ("katherine", "catherine"), "kath": ("katherine",),
    "kathy": ("katherine", "kathryn"), "kay": ("katherine",), "kev": ("kevin",),
    "ken": ("kenneth",), "kenny": ("kenneth",), "kim": ("kimberley",), "kris": ("kristopher", "christopher"),
    # L
    "larry": ("lawrence", "laurence"), "laurie": ("laura", "lawrence", "laurence"),
    "len": ("leonard",), "lenny": ("leonard",), "leo": ("leonard",), "les": ("leslie",),
    "lew": ("lewis",), "lexi": ("alexandra",), "libby": ("elizabeth",), "lily": ("lillian",),
    "lisa": ("elizabeth",), "liz": ("elizabeth",), "lizzie": ("elizabeth",), "lizzy": ("elizabeth",),
    "lou": ("louise", "louis"), "louie": ("louis",), "lucy": ("lucinda",), "luke": ("lucas",),
    # M
    "maddie": ("madeleine",), "maddy": ("madeleine",), "mags": ("margaret",), "maggie": ("margaret",),
    "madge": ("margaret",), "meg": ("margaret",), "peggy": ("margaret",), "marge": ("margaret",),
    "mandy": ("amanda",), "manny": ("emmanuel",), "marty": ("martin",), "matt": ("matthew",),
    "matty": ("matthew",), "max": ("maximilian", "maxwell"), "mel": ("melanie", "melvin"),
    "mick": ("michael",), "micky": ("michael",), "mike": ("michael",), "mikey": ("michael",),
    "millie": ("mildred", "amelia"), "milly": ("mildred", "amelia"), "molly": ("mary",),
    "mo": ("maureen", "mohammed"), "mollie": ("mary",),
    # N
    "nat": ("nathan", "nathaniel", "natalie"), "nate": ("nathan", "nathaniel"),
    "ned": ("edward",), "neil": ("cornelius",), "nell": ("eleanor", "helen"), "nellie": ("eleanor", "helen"),
    "nick": ("nicholas",), "nicky": ("nicholas", "nicola"), "nicki": ("nicola",), "nikki": ("nicola",),
    "nige": ("nigel",), "nora": ("eleanor", "honora"),
    # O-P
    "ollie": ("oliver", "olivia"), "olly": ("oliver",), "pam": ("pamela",), "pat": ("patrick", "patricia"),
    "patty": ("patricia",), "paddy": ("patrick",), "pete": ("peter",), "phil": ("philip", "phillip"),
    "pip": ("philippa",), "polly": ("mary",), "pru": ("prudence",),
    # R
    "ray": ("raymond",), "reg": ("reginald",), "reggie": ("reginald",), "rich": ("richard",),
    "richie": ("richard",), "rick": ("richard",), "ricky": ("richard",), "rob": ("robert",),
    "robbie": ("robert",), "robin": ("robert",), "rod": ("rodney", "roderick"), "rodge": ("roger",),
    "ron": ("ronald",), "ronnie": ("ronald",), "rosie": ("rosemary", "rosalind"), "roz": ("rosalind",),
    "russ": ("russell",),
    # S
    "sal": ("sally", "sarah"), "sam": ("samuel", "samantha"), "sammy": ("samuel", "samantha"),
    "sid": ("sidney",), "si": ("simon",), "sophie": ("sophia",), "stan": ("stanley",),
    "steph": ("stephanie",), "steve": ("stephen", "steven"), "stevie": ("stephen", "steven"),
    "stu": ("stuart", "stewart"), "sue": ("susan",), "susie": ("susan",), "suzy": ("susan",),
    # T
    "tam": ("thomas",), "tammy": ("tamara",), "terry": ("terence",), "tess": ("teresa",),
    "tessa": ("teresa",), "theo": ("theodore",), "tilly": ("matilda",), "tim": ("timothy",),
    "timmy": ("timothy",), "tom": ("thomas",), "tommy": ("thomas",), "toni": ("antonia",),
    "tony": ("anthony",), "tori": ("victoria",), "tracey": ("tracy",), "trish": ("patricia",),
    # V-Z
    "val": ("valerie",), "vic": ("victor", "victoria"), "vicky": ("victoria",), "vikki": ("victoria",),
    "vince": ("vincent",), "viv": ("vivian", "vivienne"), "wally": ("walter",), "walt": ("walter",),
    "wes": ("wesley",), "will": ("william",), "willie": ("william",), "zak": ("zachary",),
    "zach": ("zachary",),
    # Common spelling variants treated as equivalent
    "steven": ("stephen",), "stephen": ("steven",), "phillip": ("philip",), "philip": ("phillip",),
    "catherine": ("katherine",), "katherine": ("catherine",), "kathryn": ("katherine",),
    "jeffrey": ("geoffrey",), "geoffrey": ("jeffrey",), "mohammad": ("mohammed",),
    "muhammad": ("mohammed",), "mohamed": ("mohammed",), "muhammed": ("mohammed",),
    "sean": ("shaun", "shawn"), "shaun": ("sean",), "shawn": ("sean",), "isobel": ("isabel",),
    "isabelle": ("isabel",), "lewis": ("louis",), "louis": ("lewis",), "aiden": ("aidan",),
    "aidan": ("aiden",),
}

NICKNAMES: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in _RAW.items()}


def first_name_forms(first_key: str) -> frozenset[str]:
    """The record's own forename key plus any formal forms it maps to."""
    if not first_key or len(first_key) < 2:
        return frozenset()
    return frozenset({first_key}) | NICKNAMES.get(first_key, frozenset())
