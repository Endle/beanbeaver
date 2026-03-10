use std::error::Error;

mod tui;

#[derive(Debug, Default, PartialEq, Eq)]
struct CliArgs {
    quit_after_launch: bool,
}

fn parse_args<I>(args: I) -> Result<CliArgs, String>
where
    I: IntoIterator,
    I::Item: Into<String>,
{
    let mut parsed = CliArgs::default();
    for arg in args.into_iter().skip(1).map(Into::into) {
        match arg.as_str() {
            "--quit-after-launch" => parsed.quit_after_launch = true,
            "--help" | "-h" => {
                return Err(
                    "Usage: bb-tui [--quit-after-launch]\n\n  --quit-after-launch  Start, render once, and exit cleanly.".to_string(),
                )
            }
            _ => return Err(format!("Unknown argument: {arg}")),
        }
    }
    Ok(parsed)
}

fn main() -> Result<(), Box<dyn Error>> {
    let args = match parse_args(std::env::args()) {
        Ok(args) => args,
        Err(message) => {
            if message.starts_with("Usage: ") {
                println!("{message}");
                return Ok(());
            }
            eprintln!("{message}");
            eprintln!("Run `bb-tui --help` for usage.");
            return Err(message.into());
        }
    };
    tui::run(args.quit_after_launch)
}

#[cfg(test)]
mod tests {
    use super::{parse_args, CliArgs};

    #[test]
    fn parses_quit_after_launch_flag() {
        let args = parse_args(["bb-tui", "--quit-after-launch"]).unwrap();
        assert_eq!(
            args,
            CliArgs {
                quit_after_launch: true,
            }
        );
    }

    #[test]
    fn rejects_unknown_flag() {
        let error = parse_args(["bb-tui", "--bogus"]).unwrap_err();
        assert_eq!(error, "Unknown argument: --bogus");
    }
}
