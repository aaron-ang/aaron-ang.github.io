import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";

const navData = ["About", "Projects", "Skills"];

const Navigation = ({ onClick }) => {
  return (
    <Stack spacing={2} direction="row" justifyContent="center" mt={5}>
      {navData.map((nav) => (
        <Button variant="contained" id={nav.toLowerCase()} onClick={onClick}>
          {nav}
        </Button>
      ))}
    </Stack>
  );
};

export default Navigation;
